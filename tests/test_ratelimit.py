"""Tests for app/utils/ratelimit.py (#167).

Hermetic: FakeRedis-backed, no real Redis/network. The decorator binds
``get_l2`` via ``from app.cache import get_l2``, so the fake is patched in
the ``app.utils.ratelimit`` namespace. The module-level Lua-script cache
(``_script_obj``) is reset per test so fakes never leak across tests.
"""

import pytest
from flask import Flask, jsonify

import app.utils.ratelimit as rl


class FakeScript:
    """Mimic the Redis Lua token bucket: allow `capacity` calls, then deny."""

    def __init__(self, capacity=2):
        self.calls = 0
        self.capacity = capacity

    def __call__(self, keys=None, args=None):
        capacity = int(args[0]) if args else self.capacity
        self.calls += 1
        if self.calls <= capacity:
            return [1, float(capacity - self.calls)]
        return [0, 0.0]


class FakeRedis:
    def __init__(self, script=None):
        self._script = script or FakeScript()

    def register_script(self, src):
        return self._script


class FailingRedis:
    """register_script succeeds but the script itself raises (Redis error)."""

    class _FailingScript:
        def __call__(self, keys=None, args=None):
            raise Exception("redis down")

    def register_script(self, src):
        return FailingRedis._FailingScript()


@pytest.fixture()
def _fresh_limiter(monkeypatch):
    """Reset the cached Lua script object and pin RATELIMIT_ENABLED=true."""
    monkeypatch.setattr(rl, "_script_obj", None)
    monkeypatch.setenv("RATELIMIT_ENABLED", "true")
    yield


def _make_app(testing=False):
    app = Flask(__name__)
    app.config["TESTING"] = testing

    @app.route("/ping")
    @rl.rate_limit(capacity=2, refill_rate=1.0, key_func=lambda: "test-key")
    def ping():
        return jsonify({"ok": True})

    return app


def test_allow_then_429_with_headers(_fresh_limiter, monkeypatch):
    fake = FakeRedis(FakeScript(capacity=2))
    monkeypatch.setattr(rl, "get_l2", lambda: fake)
    client = _make_app().test_client()

    first = client.get("/ping")
    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "2"
    assert first.headers["X-RateLimit-Remaining"] == "1"

    second = client.get("/ping")
    assert second.status_code == 200
    assert second.headers["X-RateLimit-Remaining"] == "0"

    limited = client.get("/ping")
    assert limited.status_code == 429
    assert limited.get_json()["error"] == "Rate limit exceeded"
    assert limited.headers["Retry-After"] == "1"
    assert limited.headers["X-RateLimit-Limit"] == "2"


def test_fail_open_on_redis_exception(_fresh_limiter, monkeypatch):
    monkeypatch.setattr(rl, "get_l2", lambda: FailingRedis())
    response = _make_app().test_client().get("/ping")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_fail_open_when_l2_unavailable(_fresh_limiter, monkeypatch):
    monkeypatch.setattr(rl, "get_l2", lambda: None)
    response = _make_app().test_client().get("/ping")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_disabled_via_env_bypass(_fresh_limiter, monkeypatch):
    monkeypatch.setenv("RATELIMIT_ENABLED", "false")

    def _must_not_run():
        raise AssertionError("get_l2 must not be called when disabled")

    monkeypatch.setattr(rl, "get_l2", _must_not_run)
    response = _make_app().test_client().get("/ping")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_testing_config_bypass(_fresh_limiter, monkeypatch):
    """Under app TESTING the decorator never touches Redis (#167)."""

    def _must_not_run():
        raise AssertionError("get_l2 must not be called under TESTING")

    monkeypatch.setattr(rl, "get_l2", _must_not_run)
    response = _make_app(testing=True).test_client().get("/ping")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
