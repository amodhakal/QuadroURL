"""Pins for the fail-open vs fail-closed policy (docs/failure-modes.md).

Only behaviors NOT already pinned by test_readiness.py, test_cache_l2.py, or
test_cache_stampede.py are covered here:

- already pinned: /ready 200/503 per dependency, /health isolation,
  _produce backpressure raise + retry, KAFKA_SYNC_FALLBACK DB write,
  status-store 503, L2 negative/positive paths, fire-and-forget submit guard.
- pinned here: L2 read-through-to-DB, L2 write L1-authoritative, rate-limiter
  fail-open (unavailable / error / env kill-switch / TESTING bypass),
  track_metrics and click-tracking best-effort, _produce re-raise of
  non-BufferError, /fail default-disabled.
"""

import pytest

import app.cache as cache


@pytest.fixture(autouse=True)
def _reset_cache_and_ratelimit():
    """Isolate L1/inflight/L2 flags and the cached limiter script."""
    from app.utils import ratelimit as rl

    cache._l1.clear()
    cache._inflight.clear()
    cache._l2 = None
    cache._l2_pool = None
    cache._l2_unavailable = False
    rl._script_obj = None
    yield
    cache._l1.clear()
    cache._inflight.clear()
    cache._l2 = None
    cache._l2_pool = None
    cache._l2_unavailable = False
    rl._script_obj = None


# ---------------------------------------------------------------------------
# Cache reads fail open: L2 errors fall through to the DB
# ---------------------------------------------------------------------------


def test_l2_read_error_falls_through_to_db(monkeypatch):
    """A RedisError on L2 GET must resolve from the DB, not raise (#158)."""
    import redis

    class ReadFailingRedis:
        def get(self, key):
            raise redis.RedisError("connection refused")

        def setex(self, key, ttl, value):
            raise redis.RedisError("connection refused")

    monkeypatch.setattr(cache, "get_l2", lambda: ReadFailingRedis())
    monkeypatch.setattr(cache, "_fetch_user", lambda uid: {"id": uid, "username": "db_user"})

    assert cache.get_user(7) == {"id": 7, "username": "db_user"}


# ---------------------------------------------------------------------------
# Cache writes fail open: L1 stays authoritative when L2 is down
# ---------------------------------------------------------------------------


def test_l2_write_failure_keeps_l1_authoritative(monkeypatch):
    """set/delete with L2 down must still update L1 and never raise (#158)."""
    monkeypatch.setattr(cache, "get_l2", lambda: None)

    cache.set_user(1, {"id": 1, "username": "w"}, ttl=300)
    assert cache._l1_get("user:1")[0] == {"id": 1, "username": "w"}

    cache.delete_user(1)
    assert cache._l1_get("user:1")[0] is None


# ---------------------------------------------------------------------------
# Rate limiter fails open
# ---------------------------------------------------------------------------


def _ok_view():
    return {"ok": True}


def test_ratelimit_allows_when_redis_unavailable(monkeypatch):
    """get_l2() -> None must allow the request (#158)."""
    from app.utils import ratelimit as rl

    monkeypatch.setattr(rl, "get_l2", lambda: None)

    decorated = rl.rate_limit(capacity=1, refill_rate=1.0, key_func=lambda: "k")(_ok_view)
    assert decorated() == {"ok": True}


def test_ratelimit_allows_on_redis_error(monkeypatch):
    """A Redis script error must log and allow the request (#158)."""
    import redis

    from app.utils import ratelimit as rl

    class ExplodingScripts:
        def register_script(self, script):
            def _call(*args, **kwargs):
                raise redis.RedisError("boom")

            return _call

    monkeypatch.setattr(rl, "get_l2", lambda: ExplodingScripts())

    decorated = rl.rate_limit(capacity=1, refill_rate=1.0, key_func=lambda: "k")(_ok_view)
    assert decorated() == {"ok": True}


def test_ratelimit_disabled_by_env_bypass(monkeypatch):
    """RATELIMIT_ENABLED=false must bypass Redis entirely (#158)."""
    from app.utils import ratelimit as rl

    monkeypatch.setenv("RATELIMIT_ENABLED", "false")

    def boom():
        raise AssertionError("Redis must not be touched when disabled")

    monkeypatch.setattr(rl, "get_l2", boom)

    decorated = rl.rate_limit(capacity=1, refill_rate=1.0, key_func=lambda: "k")(_ok_view)
    assert decorated() == {"ok": True}


def test_ratelimit_bypassed_under_testing(app, monkeypatch):
    """Flask TESTING config must bypass the limiter so the suite never 429s."""
    from app.utils import ratelimit as rl

    def boom():
        raise AssertionError("Redis must not be touched under TESTING")

    monkeypatch.setattr(rl, "get_l2", boom)

    decorated = rl.rate_limit(capacity=1, refill_rate=1.0, key_func=lambda: "k")(_ok_view)
    with app.app_context():
        assert app.config.get("TESTING") is True
        assert decorated() == {"ok": True}


# ---------------------------------------------------------------------------
# Observability fails open
# ---------------------------------------------------------------------------


def test_request_succeeds_when_log_publish_fails(client, sample_user, monkeypatch):
    """track_metrics must swallow publish_log_event errors (response intact)."""
    import app as app_pkg

    def boom(payload):
        raise RuntimeError("kafka down")

    monkeypatch.setattr(app_pkg, "publish_log_event", boom)

    response = client.get(f"/users/{sample_user.id}")
    assert response.status_code == 200
    assert response.get_json()["username"] == sample_user.username


def test_redirect_succeeds_when_click_tracking_fails(client, sample_url, monkeypatch):
    """track_click is best-effort: event-sink failure must not break redirects."""
    import app.routes.urls as urls_mod

    def boom(*args, **kwargs):
        raise RuntimeError("event sink down")

    monkeypatch.setattr(urls_mod, "create_event", boom)

    response = client.get(f"/r/{sample_url.short_code}")
    assert response.status_code == 200
    assert response.get_json()["url"] == sample_url.original_url


# ---------------------------------------------------------------------------
# Writes fail closed: unexpected Kafka errors propagate
# ---------------------------------------------------------------------------


def test_produce_reraises_unexpected_kafka_errors(monkeypatch):
    """Non-BufferError from the producer must propagate, never drop (#158)."""
    from app.utils import kafka_producer as kp

    class ExplodingProducer:
        def produce(self, *args, **kwargs):
            raise RuntimeError("broker gone")

        def poll(self, timeout=0):
            return None

    monkeypatch.setattr(kp, "_get_producer", lambda: ExplodingProducer())

    with pytest.raises(RuntimeError, match="broker gone"):
        kp._produce("test-topic", {"a": 1})


# ---------------------------------------------------------------------------
# Chaos kill-switch is closed by default
# ---------------------------------------------------------------------------


def test_fail_route_disabled_by_default_returns_404(client, monkeypatch):
    """GET /fail without CHAOS_ENABLED=true must 404 (#100, #158)."""
    monkeypatch.setenv("CHAOS_ENABLED", "false")
    monkeypatch.delenv("CHAOS_TOKEN", raising=False)

    assert client.get("/fail").status_code == 404
