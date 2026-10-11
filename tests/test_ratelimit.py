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
    rl.reset_local_buckets()
    yield
    rl.reset_local_buckets()


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


def test_local_fallback_enforces_limit_when_redis_errors(_fresh_limiter, monkeypatch):
    """A Redis outage must not turn the limiter off (#300)."""
    monkeypatch.setattr(rl, "get_l2", lambda: FailingRedis())
    client = _make_app().test_client()

    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    limited = client.get("/ping")
    assert limited.status_code == 429
    assert limited.headers["X-RateLimit-Limit"] == "2"


def test_local_fallback_enforces_limit_when_l2_unavailable(_fresh_limiter, monkeypatch):
    monkeypatch.setattr(rl, "get_l2", lambda: None)
    client = _make_app().test_client()

    statuses = [client.get("/ping").status_code for _ in range(3)]
    assert statuses == [200, 200, 429]


def test_fail_closed_is_the_default():
    import inspect

    assert inspect.signature(rl.rate_limit).parameters["fail_closed"].default is True


def test_explicit_fail_open_still_allows_through(_fresh_limiter, monkeypatch):
    monkeypatch.setattr(rl, "get_l2", lambda: None)

    app = Flask(__name__)
    app.config["TESTING"] = False

    @app.route("/ping")
    @rl.rate_limit(capacity=1, refill_rate=1.0, key_func=lambda: "open-key", fail_closed=False)
    def ping():
        return jsonify({"ok": True})

    client = app.test_client()
    assert [client.get("/ping").status_code for _ in range(3)] == [200, 200, 200]


def test_503_when_local_fallback_cannot_be_built(_fresh_limiter, monkeypatch):
    monkeypatch.setattr(rl, "get_l2", lambda: None)
    monkeypatch.setattr(rl, "get_local_bucket", lambda *a, **kw: None)

    response = _make_app().test_client().get("/ping")
    assert response.status_code == 503
    assert response.get_json()["error"] == "Rate limiter unavailable"


def test_local_bucket_refills_over_time():
    bucket = rl.LocalTokenBucket(capacity=2, refill_rate=1.0, ttl=60)
    assert bucket.consume("k", now=0.0)[0] is True
    assert bucket.consume("k", now=0.0)[0] is True
    assert bucket.consume("k", now=0.0)[0] is False
    assert bucket.consume("k", now=1.0)[0] is True


def test_local_bucket_rejects_invalid_settings():
    assert rl.get_local_bucket(0, 1.0) is None
    assert rl.get_local_bucket(10, -1) is None


def test_local_bucket_evicts_idle_keys():
    bucket = rl.LocalTokenBucket(capacity=1, refill_rate=1.0, ttl=1)
    for i in range(1100):
        bucket.consume(f"key-{i}", now=float(i))
    assert len(bucket._buckets) <= 1024


def test_client_ip_ignores_forwarded_headers_without_trusted_proxies(_fresh_limiter):
    from app.utils.request_ctx import get_client_ip

    app = Flask(__name__)
    app.config["TRUSTED_PROXY_COUNT"] = 0
    with app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": "10.0.0.7"},
        headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"},
    ):
        assert get_client_ip() == "10.0.0.7"


@pytest.mark.parametrize(
    "trusted,xff,expected",
    [
        (1, "1.2.3.4", "1.2.3.4"),
        (1, "9.9.9.9, 1.2.3.4", "1.2.3.4"),
        (2, "9.9.9.9, 1.2.3.4", "9.9.9.9"),
    ],
)
def test_client_ip_uses_trusted_proxy_hops(trusted, xff, expected):
    from app.utils.request_ctx import get_client_ip

    app = Flask(__name__)
    app.config["TRUSTED_PROXY_COUNT"] = trusted
    with app.test_request_context(
        "/", environ_base={"REMOTE_ADDR": "10.0.0.7"}, headers={"X-Forwarded-For": xff}
    ):
        assert get_client_ip() == expected


def test_client_ip_falls_back_to_remote_addr_on_short_chain():
    from app.utils.request_ctx import get_client_ip

    app = Flask(__name__)
    app.config["TRUSTED_PROXY_COUNT"] = 2
    with app.test_request_context(
        "/", environ_base={"REMOTE_ADDR": "10.0.0.7"}, headers={"X-Forwarded-For": "1.2.3.4"}
    ):
        assert get_client_ip() == "10.0.0.7"
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "10.0.0.7"}):
        assert get_client_ip() == "10.0.0.7"


def test_client_ip_reads_trusted_proxy_count_from_env(monkeypatch):
    from app.utils.request_ctx import get_client_ip

    app = Flask(__name__)
    app.config.pop("TRUSTED_PROXY_COUNT", None)
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
    with app.test_request_context(
        "/", environ_base={"REMOTE_ADDR": "10.0.0.7"}, headers={"X-Forwarded-For": "1.2.3.4"}
    ):
        assert get_client_ip() == "1.2.3.4"


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
