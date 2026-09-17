"""API versioning parity (#151).

Exercises the real ``register_routes`` wiring: every resource blueprint
serves its routes in both flavors — the legacy unprefixed shape and
``/api/v1``. Operational endpoints (health, metrics, dashboard, Prometheus,
fail) are intentionally unversioned. Docs ship both ``/openapi.json`` and
``/api/v1/openapi.json``.
"""

import pytest
from flask import Flask, jsonify
from peewee import SqliteDatabase
from werkzeug.exceptions import HTTPException

from app.models import ApiKey, Event, RequestLog, Url, User

RESOURCE_BLUEPRINTS = {
    "users",
    "urls",
    "events",
    "auth",
    "exports",
    "analytics",
    "delivery",
    "links",
    "qr",
    "logs",
}


@pytest.fixture(autouse=True)
def clean_tables():
    yield


@pytest.fixture()
def versioned_client(tmp_path):
    database = SqliteDatabase(str(tmp_path / "version151.db"), pragmas={"foreign_keys": 1})
    from app.database import models as schema
    from app.routes import delivery as delivery_module
    from app.routes import register_routes
    from app.utils.auth import issue_api_key

    delivery = delivery_module.delivery
    tables = [
        User,
        Url,
        Event,
        ApiKey,
        RequestLog,
        schema.LinkMetadata,
        delivery.DeadLetter,
        delivery.Receipt,
        delivery.Replay,
        delivery.Subscription,
        delivery.Delivery,
    ]
    with database.bind_ctx(tables):
        database.create_tables(tables)
        instance = Flask(__name__)
        instance.config.update(TESTING=True)
        register_routes(instance)

        @instance.errorhandler(HTTPException)
        def error(exc):
            return jsonify(error=exc.description), exc.code

        owner = User.create(username="versioned", email="versioned@example.com")
        _, key = issue_api_key(owner.id)
        client = instance.test_client()
        client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {key}"
        yield client
        database.close()


def _flavor(rule):
    if rule.startswith("/api/v1/"):
        return rule.removeprefix("/api/v1"), "v1"
    return rule, "legacy"


def test_every_resource_route_has_legacy_and_v1_flavors(versioned_client):
    by_path = {}
    for rule in versioned_client.application.url_map.iter_rules():
        if "static" in rule.endpoint:
            continue
        path, flavor = _flavor(str(rule))
        blueprint = rule.endpoint.split(".")[0].removesuffix("_v1")
        by_path.setdefault((blueprint, path), set()).add(flavor)
    missing = [
        f"{blueprint} {path} lacks { {'legacy', 'v1'} - flavors }"
        for (blueprint, path), flavors in sorted(by_path.items())
        if not {"legacy", "v1"} <= flavors and blueprint in RESOURCE_BLUEPRINTS
    ]
    assert missing == []


def test_v1_and_legacy_serve_side_by_side(versioned_client):
    headers = {"Authorization": versioned_client.environ_base["HTTP_AUTHORIZATION"]}
    legacy = versioned_client.get("/urls?size=1", headers=headers)
    versioned = versioned_client.get("/api/v1/urls?size=1", headers=headers)
    assert legacy.status_code == 200 and versioned.status_code == 200
    assert set(legacy.json) == {"kind", "sample"}
    assert set(versioned.json) == {"items", "pagination"}
    spec_legacy = versioned_client.get("/openapi.json")
    spec_v1 = versioned_client.get("/api/v1/openapi.json")
    assert spec_legacy.status_code == 200 and spec_v1.status_code == 200
    assert spec_legacy.json == spec_v1.json
