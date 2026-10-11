"""Safe Browsing verdicts are cached so redirects never block on the provider (#301)."""

import io
import time
from unittest.mock import MagicMock

import pytest
import requests
from werkzeug.exceptions import Forbidden, ServiceUnavailable

from app import cache
from app.utils.url_safety import (
    _verdict_cache_key,
    _verdict_cache_ttl,
    reputation_status,
    require_destination_safe,
)


def provider(monkeypatch, app, body=b"{}", status=200):
    """Stub the Safe Browsing HTTP client and return the session mock."""
    app.config.update(SAFE_BROWSING_PROVIDER="google", SAFE_BROWSING_API_KEY="test-key")
    session = MagicMock()
    session.__enter__.return_value = session
    response = MagicMock()
    response.status_code = status
    raw_read = io.BytesIO(body).read
    response.raw = MagicMock()
    response.raw.read.side_effect = lambda n, **kw: raw_read(n)
    session.post.return_value.__enter__.return_value = response
    monkeypatch.setattr("app.utils.url_safety.requests.Session", lambda: session)
    return session


def l1_remaining(key):
    """Seconds left on an L1 entry, or None when the key is absent."""
    with cache._l1_lock:
        entry = cache._l1.get(key)
    return None if entry is None else entry[1] - time.time()


@pytest.mark.parametrize("body,expected", [(b"{}", "no_match"), (b'{"matches":[{}]}', "unsafe")])
def test_verdicts_are_cached_so_redirects_never_call_the_provider(app, monkeypatch, body, expected):
    session = provider(monkeypatch, app, body)
    url = f"https://cached-{expected}.example.com/path?q=secret"
    with app.app_context():
        assert reputation_status(url) == expected
        assert session.post.call_count == 1
        for _ in range(5):
            assert reputation_status(url) == expected
    assert session.post.call_count == 1, "a cached verdict must not re-hit the provider"


def test_unsafe_verdicts_are_cached_too_and_still_block(app, monkeypatch):
    session = provider(monkeypatch, app, b'{"matches":[{}]}')
    with app.app_context():
        for _ in range(3):
            with pytest.raises(Forbidden):
                require_destination_safe("https://blocked.example.com/")
    assert session.post.call_count == 1


def test_cache_keys_are_url_hashes_never_raw_urls(app, monkeypatch):
    provider(monkeypatch, app)
    with app.app_context():
        reputation_status("https://privacy.example.com/?token=hunter2")
    keys = [key for key in list(cache._l1) if key.startswith("reputation:v1:")]
    assert keys, "the verdict should be cached"
    for key in keys:
        assert "privacy.example.com" not in key
        assert "hunter2" not in key
        digest = key.split("reputation:v1:")[1]
        assert len(digest) == 64  # sha256 hex


def test_ttl_is_configurable_and_zero_disables_caching(app, monkeypatch):
    session = provider(monkeypatch, app)
    app.config["SAFE_BROWSING_CACHE_TTL"] = 60
    with app.app_context():
        reputation_status("https://ttl.example.com/")
        remaining = l1_remaining(_verdict_cache_key("https://ttl.example.com/"))
        assert 54 <= remaining <= 66  # 60s TTL, +/- 10 % L1 jitter
        reputation_status("https://ttl.example.com/")
    assert session.post.call_count == 1

    app.config["SAFE_BROWSING_CACHE_TTL"] = 0
    with app.app_context():
        reputation_status("https://nocache.example.com/")
        reputation_status("https://nocache.example.com/")
    assert session.post.call_count == 3
    assert l1_remaining(_verdict_cache_key("https://nocache.example.com/")) is None


def test_default_ttl_is_one_hour_and_bad_values_fall_back(app, monkeypatch):
    provider(monkeypatch, app)
    with app.app_context():
        app.config.pop("SAFE_BROWSING_CACHE_TTL", None)
        assert _verdict_cache_ttl() == 3600
        for bad in ("not-a-number", None, []):
            app.config["SAFE_BROWSING_CACHE_TTL"] = bad
            assert _verdict_cache_ttl() == 3600


def test_in_process_fallback_caches_when_redis_is_unavailable(app, monkeypatch):
    """No Redis: the bounded in-process cache still absorbs repeat lookups."""
    monkeypatch.setattr(cache, "get_l2", lambda: None)
    session = provider(monkeypatch, app)
    with app.app_context():
        for _ in range(4):
            assert reputation_status("https://no-redis.example.com/") == "no_match"
    assert session.post.call_count == 1


def test_redis_verdict_hit_is_promoted_into_the_in_process_cache(app, monkeypatch):
    monkeypatch.setattr(cache, "get_l2", lambda: _FakeRedis("no_match"))
    provider(monkeypatch, app)
    with app.app_context():
        assert reputation_status("https://l2-only.example.com/") == "no_match"
    assert l1_remaining(_verdict_cache_key("https://l2-only.example.com/")) > 0


def test_provider_failures_are_not_cached_and_still_follow_failure_policy(app, monkeypatch):
    session = provider(monkeypatch, app)
    session.post.side_effect = requests.Timeout()
    with app.app_context():
        # A genuine provider failure is re-evaluated on every call, so a
        # fail-open policy takes effect immediately instead of after a TTL.
        with pytest.raises(ServiceUnavailable):
            require_destination_safe("https://flaky.example.com/")
        app.config["SAFE_BROWSING_FAILURE_POLICY"] = "open"
        assert require_destination_safe("https://flaky.example.com/") == "unavailable"
    assert session.post.call_count == 2
    assert l1_remaining(_verdict_cache_key("https://flaky.example.com/")) is None


def test_cached_verdict_is_not_a_failure(app, monkeypatch):
    """A cached verdict never trips the fail-closed 503 path."""
    provider(monkeypatch, app, b"{}")
    with app.app_context():
        assert require_destination_safe("https://warm.example.com/") == "no_match"
        monkeypatch.setattr(
            "app.utils.url_safety.requests.Session",
            lambda: pytest.fail("provider must not be called on a cache hit"),
        )
        assert require_destination_safe("https://warm.example.com/") == "no_match"


def test_disabled_provider_is_never_cached(app, monkeypatch):
    session = provider(monkeypatch, app)
    app.config["SAFE_BROWSING_PROVIDER"] = "disabled"
    with app.app_context():
        assert reputation_status("https://off.example.com/") == "disabled"
    assert session.post.call_count == 0
    assert not [key for key in list(cache._l1) if key.startswith("reputation:v1:")]


class _FakeRedis:
    """Minimal stand-in for the Redis client used by ``_l2_safe``."""

    def __init__(self, stored):
        self.stored = stored
        self.writes = []

    def get(self, key):
        return self.stored

    def setex(self, key, ttl, value):
        self.writes.append((key, ttl, value))
