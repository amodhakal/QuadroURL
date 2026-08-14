"""Tests for uncovered edge paths in app.cache (encoder, L2, delete/clear)."""

import datetime
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

import app.cache as cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cache_state():
    """Reset L1, inflight, and L2 connection state before/after each test.

    Storing previous values is unnecessary; force everything back to defaults.
    """
    cache._l1.clear()
    cache._inflight.clear()
    cache._l2 = None
    cache._l2_pool = None
    cache._l2_unavailable = False
    yield
    cache._l1.clear()
    cache._inflight.clear()
    cache._l2 = None
    cache._l2_pool = None
    cache._l2_unavailable = False


class FakeRedis:
    """In-memory stand-in for a Redis client used only where get_l2 is
    monkeypatched, so no real redis connection is ever attempted."""

    def __init__(self, data=None):
        self.data = data if data is not None else {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value

    def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)

    def keys(self, pattern):
        return [k for k in self.data if k.startswith(pattern.replace("*", ""))]


def _backdate_stale(key, value, ttl=1):
    """Write an entry into L1 that has crossed the early-expiry threshold."""
    with cache._l1_lock:
        cache._l1[key] = (
            value,
            time.time() + (ttl * 0.1),
            time.time() - (ttl * 0.9),
            ttl,
        )


# ---------------------------------------------------------------------------
# _Encoder
# ---------------------------------------------------------------------------

def test_encoder_default_non_datetime_raises():
    """_Encoder.default on a non-datetime object should raise TypeError."""
    enc = cache._Encoder()
    with pytest.raises(TypeError):
        enc.default(object())


def test_encoder_default_datetime_is_isoformat():
    """_Encoder.default on a datetime should return an isoformat string."""
    enc = cache._Encoder()
    now = datetime.datetime.now()
    result = enc.default(now)
    assert isinstance(result, str)
    assert result == now.isoformat()


# ---------------------------------------------------------------------------
# _l1_get hard expiry
# ---------------------------------------------------------------------------

def test_l1_get_hard_expiry_pops_entry():
    """A negative TTL entry expires immediately and is popped on read."""
    cache._l1_set("k", "v", ttl=-1)
    value, is_stale = cache._l1_get("k")
    assert value is None
    assert is_stale is False
    assert "k" not in cache._l1


# ---------------------------------------------------------------------------
# get_l2
# ---------------------------------------------------------------------------

def test_get_l2_unavailable_flag_returns_none():
    cache._l2_unavailable = True
    assert cache.get_l2() is None


def test_get_l2_connection_error_sets_unavailable(monkeypatch):
    """A ConnectionError while building the pool should mark L2 unavailable."""
    cache._l2 = None
    cache._l2_unavailable = False
    called = {}

    def raise_conn_error(*args, **kwargs):
        called["boom"] = True
        raise cache.redis.ConnectionError("boom")

    monkeypatch.setattr(cache.redis.ConnectionPool, "from_url", raise_conn_error)
    assert cache.get_l2() is None
    assert cache._l2_unavailable is True
    assert called.get("boom") is True


# ---------------------------------------------------------------------------
# _resolve_miss
# ---------------------------------------------------------------------------

def test_resolve_miss_negative_sentinel_in_l1(monkeypatch):
    """_resolve_miss returns None immediately when L1 holds the sentinel."""
    cache._l1_set("k", cache._NEGATIVE_SENTINEL, ttl=10)
    called = {"n": 0}

    def fetch_fn():
        called["n"] += 1
        return {"should": "not-fetch"}

    result = cache._resolve_miss("k", fetch_fn, 300)
    assert result is None
    assert called["n"] == 0


def test_resolve_miss_concurrent_waiters_read_sentinel():
    """Concurrent callers waiting on a negative miss should all get None."""
    call_count = 0
    call_lock = threading.Lock()

    def fetch_fn():
        nonlocal call_count
        with call_lock:
            call_count += 1
        time.sleep(0.1)
        return None

    results = []
    threads = []

    def worker():
        results.append(cache._resolve_miss("miss:key", fetch_fn, ttl=300))

    for _ in range(5):
        t = threading.Thread(target=worker)
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert call_count == 1
    assert all(r is None for r in results)


# ---------------------------------------------------------------------------
# _background_refresh
# ---------------------------------------------------------------------------

def test_background_refresh_non_primary_returns(monkeypatch):
    """_background_refresh returns immediately when another refresh holds the lock."""
    cache._l1_set("k", "v", ttl=300)
    cache._acquire_inflight("k")  # become primary, holding the event
    submitted = {"n": 0}
    monkeypatch.setattr(cache._executor, "submit", lambda fn: submitted.__setitem__("n", submitted["n"] + 1))
    cache._background_refresh("k", lambda: "x", 300)
    assert submitted["n"] == 0


def test_background_refresh_negative_path():
    """_background_refresh with a None fetch stores the negative sentinel."""
    done = threading.Event()

    def fetch_fn():
        done.set()
        return None

    _backdate_stale("k", {"old": "value"})
    cache._background_refresh("k", fetch_fn, ttl=300)
    done.wait(timeout=5)
    value, _ = cache._l1_get("k")
    assert value is cache._NEGATIVE_SENTINEL


# ---------------------------------------------------------------------------
# get_user / get_url / get_url_by_short_code stale refresh
# ---------------------------------------------------------------------------

def test_get_user_stale_triggers_background_refresh(monkeypatch):
    _backdate_stale("user:1", {"id": 1, "username": "old"})
    mock = MagicMock()
    monkeypatch.setattr(cache, "_background_refresh", mock)
    result = cache.get_user(1)
    assert result == {"id": 1, "username": "old"}
    mock.assert_called_once()


def test_get_url_stale_triggers_background_refresh(monkeypatch):
    _backdate_stale("url:5", {"id": 5, "short_code": "abc"})
    mock = MagicMock()
    monkeypatch.setattr(cache, "_background_refresh", mock)
    result = cache.get_url(5)
    assert result == {"id": 5, "short_code": "abc"}
    mock.assert_called_once()


def test_get_url_by_short_code_stale_triggers_refresh(monkeypatch):
    _backdate_stale("short_code:abc", {"id": 5, "short_code": "abc"})
    mock = MagicMock()
    monkeypatch.setattr(cache, "_background_refresh", mock)
    result = cache.get_url_by_short_code("abc")
    assert result == {"id": 5, "short_code": "abc"}
    mock.assert_called_once()


# ---------------------------------------------------------------------------
# L2 negative + positive paths (get_user / get_url / get_url_by_short_code)
# ---------------------------------------------------------------------------

def test_get_user_l2_negative_and_positive(monkeypatch):
    fake = FakeRedis({"user:9": "null",
                      "user:10": json.dumps({"id": 10, "username": "u"})})
    monkeypatch.setattr(cache, "get_l2", lambda: fake)

    result = cache.get_user(9)
    assert result is None
    assert cache._l1_get("user:9")[0] is cache._NEGATIVE_SENTINEL

    result = cache.get_user(10)
    assert result == {"id": 10, "username": "u"}
    assert cache._l1_get("user:10")[0] == {"id": 10, "username": "u"}


def test_get_url_l2_negative_and_positive(monkeypatch):
    fake = FakeRedis({"url:9": "null",
                      "url:10": json.dumps({"id": 10, "short_code": "x"})})
    monkeypatch.setattr(cache, "get_l2", lambda: fake)

    assert cache.get_url(9) is None
    assert cache._l1_get("url:9")[0] is cache._NEGATIVE_SENTINEL
    assert cache.get_url(10) == {"id": 10, "short_code": "x"}
    assert cache._l1_get("url:10")[0] == {"id": 10, "short_code": "x"}


def test_get_url_by_short_code_l2_negative_and_positive(monkeypatch):
    fake = FakeRedis({"short_code:n": "null",
                      "short_code:p": json.dumps({"id": 11, "short_code": "p"})})
    monkeypatch.setattr(cache, "get_l2", lambda: fake)

    assert cache.get_url_by_short_code("n") is None
    assert cache._l1_get("short_code:n")[0] is cache._NEGATIVE_SENTINEL
    assert cache.get_url_by_short_code("p") == {"id": 11, "short_code": "p"}
    assert cache._l1_get("short_code:p")[0] == {"id": 11, "short_code": "p"}


# ---------------------------------------------------------------------------
# delete / clear helpers
# ---------------------------------------------------------------------------

def test_delete_url_by_short_code_removes_from_l1():
    cache._l1_set("short_code:abc", {"id": 1}, ttl=300)
    cache.delete_url_by_short_code("abc")
    assert cache._l1_get("short_code:abc")[0] is None


def test_clear_all_urls_removes_matching_l1_keys():
    cache._l1_set("url:1", {"id": 1}, ttl=300)
    cache._l1_set("url:2", {"id": 2}, ttl=300)
    cache._l1_set("user:1", {"id": 1}, ttl=300)
    cache.clear_all_urls()
    assert cache._l1_get("url:1")[0] is None
    assert cache._l1_get("url:2")[0] is None
    assert cache._l1_get("user:1")[0] == {"id": 1}


# ---------------------------------------------------------------------------
# _l2_fire_and_forget exception guard
# ---------------------------------------------------------------------------

def test_l2_fire_and_forget_survives_submit_exception(monkeypatch):
    """A failure in _executor.submit should not propagate to the caller."""

    def boom(*args, **kwargs):
        raise RuntimeError("submit failed")

    monkeypatch.setattr(cache._executor, "submit", boom)
    cache._l2_fire_and_forget(lambda client: client.delete("k"))


def test_resolve_miss_positive_cached_value():
    """A positive value already in L1 is returned without calling fetch_fn
    (covers line 230)."""
    cache._l1_set("k", {"id": 1}, ttl=300)
    result = cache._resolve_miss(
        "k", lambda: {"id": 999}, 300
    )
    assert result == {"id": 1}


def test_get_url_by_short_code_db_fetch(app):
    """A short-code miss that falls through L1 and L2 resolves from the DB via
    _fetch_url_by_short_code (covers lines 448 and 452-461)."""
    from app.models.url import Url
    from app.models.user import User

    with app.app_context():
        u = User.create(username="scdf", email="scdf@example.com")
        Url.create(
            user=u,
            short_code="scdb1",
            original_url="https://example.com",
            title="T",
            is_active=True,
        )

    cache._l1.clear()
    result = cache.get_url_by_short_code("scdb1")
    assert result is not None
    assert result["short_code"] == "scdb1"



def test_get_url_by_short_code_negative_sentinel_l1():
    """A negative sentinel in L1 for a short code returns None (line 430)."""
    cache._l1_set("short_code:abc", cache._NEGATIVE_SENTINEL, ttl=10)
    assert cache.get_url_by_short_code("abc") is None
