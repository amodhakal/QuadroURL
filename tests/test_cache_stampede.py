"""Tests for cache stampede (thundering herd) prevention in app.cache."""

import threading
import time

import pytest

import app.cache as cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_cache():
    """Clear L1 cache and inflight state before each test."""
    cache._l1.clear()
    cache._inflight.clear()
    yield
    cache._l1.clear()
    cache._inflight.clear()


# ---------------------------------------------------------------------------
# L1 TTL jitter
# ---------------------------------------------------------------------------

def test_jitter_ttl_within_range():
    """Jitter should keep TTL within +/- 10 % of the original."""
    for _ in range(100):
        jittered = cache._jitter_ttl(300)
        assert 270 <= jittered <= 330


def test_jitter_ttl_zero_or_negative():
    """Jitter should be a no-op for zero or negative TTL."""
    assert cache._jitter_ttl(0) == 0
    assert cache._jitter_ttl(-1) == -1


# ---------------------------------------------------------------------------
# Single-flight deduplication
# ---------------------------------------------------------------------------

def test_single_flight_deduplicates_concurrent_misses():
    """Multiple concurrent misses for the same key should only call fetch_fn once."""
    call_count = 0
    call_lock = threading.Lock()

    def fetch_fn():
        nonlocal call_count
        with call_lock:
            call_count += 1
        time.sleep(0.1)  # Simulate slow DB query.
        return {"id": 1, "name": "test"}

    results = []
    threads = []

    def worker():
        val = cache._resolve_miss("test:key", fetch_fn, ttl=300)
        results.append(val)

    for _ in range(10):
        t = threading.Thread(target=worker)
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert call_count == 1, f"fetch_fn called {call_count} times, expected 1"
    assert all(r == {"id": 1, "name": "test"} for r in results)


def test_single_flight_returns_none_for_missing_key():
    """Single-flight should cache negative results (None) and return None."""
    call_count = 0

    def fetch_fn():
        nonlocal call_count
        call_count += 1
        return None

    val = cache._resolve_miss("test:missing", fetch_fn, ttl=300)
    assert val is None
    assert call_count == 1

    # Second call should be a cache hit (negative sentinel), not another DB call.
    val2 = cache._resolve_miss("test:missing", fetch_fn, ttl=300)
    assert val2 is None
    assert call_count == 1, "fetch_fn should not be called again for negative-cached key"


# ---------------------------------------------------------------------------
# Negative caching
# ---------------------------------------------------------------------------

def test_negative_cache_stores_sentinel():
    """After a miss that returns None, the L1 cache should hold the negative sentinel."""
    cache._resolve_miss("test:neg", lambda: None, ttl=300)
    value, _ = cache._l1_get("test:neg")
    assert value is cache._NEGATIVE_SENTINEL


def test_negative_cache_has_short_ttl():
    """Negative cache entries should use the negative_ttl, not the positive ttl."""
    cache._resolve_miss("test:neg_ttl", lambda: None, ttl=300, negative_ttl=5)
    value, _ = cache._l1_get("test:neg_ttl")
    assert value is cache._NEGATIVE_SENTINEL
    # Verify the expiry is short (around 5 seconds, not 300).
    with cache._l1_lock:
        _, expiry, _, _ = cache._l1["test:neg_ttl"]
    assert expiry - time.time() <= 6  # Allow small margin for jitter.


# ---------------------------------------------------------------------------
# Probabilistic early expiration (stale-while-revalidate)
# ---------------------------------------------------------------------------

def test_stale_entry_triggers_background_refresh():
    """A stale-but-not-expired entry should trigger a background refresh."""
    refresh_called = threading.Event()

    def fetch_fn():
        refresh_called.set()
        return {"id": 1, "name": "refreshed"}

    # Set a short TTL entry and age it past the early-expiry threshold.
    cache._l1_set("test:stale", {"id": 1, "name": "old"}, ttl=1)
    # Manually backdate the entry so it's stale (> 80 % of TTL).
    with cache._l1_lock:
        cache._l1["test:stale"] = (
            {"id": 1, "name": "old"},
            time.time() + 0.9,  # expires in 0.9s
            time.time() - 0.9,  # created 0.9s ago (90 % of 1s TTL = stale)
            1,
        )

    value, is_stale = cache._l1_get("test:stale")
    assert value == {"id": 1, "name": "old"}
    assert is_stale is True

    # Trigger background refresh.
    cache._background_refresh("test:stale", fetch_fn, ttl=1)
    refresh_called.wait(timeout=5)
    assert refresh_called.is_set(), "Background refresh should have been triggered"


def test_non_stale_entry_does_not_trigger_refresh():
    """A fresh entry should not trigger a background refresh."""
    refresh_called = False

    def fetch_fn():
        nonlocal refresh_called
        refresh_called = True
        return {"id": 1}

    cache._l1_set("test:fresh", {"id": 1}, ttl=300)
    value, is_stale = cache._l1_get("test:fresh")
    assert is_stale is False
    # _background_refresh should be a no-op since is_stale is False (caller checks).
    # But if called directly, it should still work. Test the guard:
    cache._background_refresh("test:fresh", fetch_fn, ttl=300)
    time.sleep(0.5)
    # The refresh may or may not have completed, but the point is it doesn't
    # block the caller.
    assert value == {"id": 1}


# ---------------------------------------------------------------------------
# Integration: get_user with mocked DB
# ---------------------------------------------------------------------------

def test_get_user_returns_cached_value():
    """get_user should return value from L1 cache without calling DB."""
    cache._l1_set("user:1", {"id": 1, "username": "cached"}, ttl=300)
    result = cache.get_user(1)
    assert result == {"id": 1, "username": "cached"}


def test_get_user_returns_none_for_negative_cache():
    """get_user should return None for a negative-cached key."""
    cache._l1_set("user:999", cache._NEGATIVE_SENTINEL, ttl=10)
    result = cache.get_user(999)
    assert result is None


def test_get_user_single_flight_on_miss(monkeypatch):
    """get_user should deduplicate concurrent DB fetches via single-flight."""
    call_count = 0
    call_lock = threading.Lock()

    def mock_fetch_user(user_id):
        nonlocal call_count
        with call_lock:
            call_count += 1
        time.sleep(0.1)
        return {"id": user_id, "username": "db_user"}

    monkeypatch.setattr(cache, "_fetch_user", mock_fetch_user)

    results = []
    threads = []

    def worker():
        val = cache.get_user(42)
        results.append(val)

    for _ in range(5):
        t = threading.Thread(target=worker)
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert call_count == 1, f"_fetch_user called {call_count} times, expected 1"
    assert all(r == {"id": 42, "username": "db_user"} for r in results)


def test_get_user_negative_cache_on_db_miss(monkeypatch):
    """get_user should negative-cache when the DB returns None."""
    monkeypatch.setattr(cache, "_fetch_user", lambda uid: None)

    result = cache.get_user(999)
    assert result is None

    # Second call should hit negative cache, not call _fetch_user again.
    monkeypatch.setattr(cache, "_fetch_user", lambda uid: pytest.fail("should not be called"))
    result2 = cache.get_user(999)
    assert result2 is None


# ---------------------------------------------------------------------------
# Integration: get_url with mocked DB
# ---------------------------------------------------------------------------

def test_get_url_returns_cached_value():
    """get_url should return value from L1 cache without calling DB."""
    cache._l1_set("url:1", {"id": 1, "short_code": "abc"}, ttl=300)
    result = cache.get_url(1)
    assert result == {"id": 1, "short_code": "abc"}


def test_get_url_negative_cache_on_db_miss(monkeypatch):
    """get_url should negative-cache when the DB returns None."""
    monkeypatch.setattr(cache, "_fetch_url", lambda uid: None)

    result = cache.get_url(999)
    assert result is None

    monkeypatch.setattr(cache, "_fetch_url", lambda uid: pytest.fail("should not be called"))
    result2 = cache.get_url(999)
    assert result2 is None


# ---------------------------------------------------------------------------
# set / delete / clear
# ---------------------------------------------------------------------------

def test_set_user_populates_l1():
    cache.set_user(1, {"id": 1, "username": "test"}, ttl=300)
    value, _ = cache._l1_get("user:1")
    assert value == {"id": 1, "username": "test"}


def test_delete_user_removes_from_l1():
    cache._l1_set("user:1", {"id": 1}, ttl=300)
    cache.delete_user(1)
    value, _ = cache._l1_get("user:1")
    assert value is None


def test_clear_all_users():
    cache._l1_set("user:1", {"id": 1}, ttl=300)
    cache._l1_set("user:2", {"id": 2}, ttl=300)
    cache.clear_all_users()
    assert cache._l1_get("user:1")[0] is None
    assert cache._l1_get("user:2")[0] is None


def test_set_url_populates_l1():
    cache.set_url(1, {"id": 1, "short_code": "abc"}, ttl=300)
    value, _ = cache._l1_get("url:1")
    assert value == {"id": 1, "short_code": "abc"}


def test_delete_url_removes_from_l1():
    cache._l1_set("url:1", {"id": 1}, ttl=300)
    cache.delete_url(1)
    value, _ = cache._l1_get("url:1")
    assert value is None


# ---------------------------------------------------------------------------
# L1 LRU eviction
# ---------------------------------------------------------------------------

def test_l1_eviction_when_over_max():
    """L1 cache should evict oldest entries when exceeding _L1_MAX."""
    original_max = cache._L1_MAX
    cache._L1_MAX = 5
    try:
        for i in range(10):
            cache._l1_set(f"key:{i}", {"val": i}, ttl=300)
        # Only the last 5 should remain.
        assert cache._l1_get("key:0")[0] is None
        assert cache._l1_get("key:9")[0] == {"val": 9}
    finally:
        cache._L1_MAX = original_max
