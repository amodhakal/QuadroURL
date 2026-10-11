"""The OpenAPI document is built once per route map and memoized (#302)."""

import pytest
from flask import Flask


@pytest.fixture(autouse=True)
def clean_tables():
    yield


@pytest.fixture()
def spec_app():
    from app.routes import register_routes

    instance = Flask(__name__)
    instance.config.update(TESTING=True)
    register_routes(instance)
    yield instance


def test_spec_is_cached_across_requests(spec_app):
    from app.routes import openapi as openapi_module

    openapi_module.invalidate_spec_cache()
    client = spec_app.test_client()

    first = client.get("/openapi.json")
    assert first.status_code == 200
    assert first.content_type == "application/json"
    assert first.get_json()["openapi"].startswith("3.1")

    cached = openapi_module._SPEC_CACHE
    assert len(cached) == 1

    calls = []
    original = openapi_module.build_spec

    def _counted():
        calls.append(1)
        return original()

    openapi_module.build_spec = _counted
    try:
        second = client.get("/openapi.json")
        alias = client.get("/api/v1/openapi.json")
    finally:
        openapi_module.build_spec = original

    assert calls == []
    assert second.get_data() == first.get_data()
    assert alias.get_data() == first.get_data()
    assert len(openapi_module._SPEC_CACHE) == 1


def test_invalidation_rebuilds_the_spec(spec_app):
    from app.routes import openapi as openapi_module

    openapi_module.invalidate_spec_cache()
    assert openapi_module._SPEC_CACHE == {}

    client = spec_app.test_client()
    baseline = client.get("/openapi.json").get_data()

    openapi_module.invalidate_spec_cache()
    assert openapi_module._SPEC_CACHE == {}
    assert client.get("/openapi.json").get_data() == baseline


def test_each_app_builds_its_own_spec():
    """The cache is keyed by route map, so a different app is not poisoned."""
    from app.routes import openapi as openapi_module
    from app.routes.openapi import docs_bp

    openapi_module.invalidate_spec_cache()

    full = Flask(__name__)
    full.config.update(TESTING=True)
    from app.routes import register_routes

    register_routes(full)

    partial = Flask(__name__)
    partial.config.update(TESTING=True)
    partial.register_blueprint(docs_bp)

    full_paths = set(full.test_client().get("/openapi.json").get_json()["paths"])
    partial_paths = set(partial.test_client().get("/openapi.json").get_json()["paths"])
    assert "/api/v1/urls" in full_paths
    assert partial_paths == set()

    openapi_module.invalidate_spec_cache()