"""Tests for the cross-worker L1 invalidation bus (app.cache, #118)."""

import threading
import time

import pytest
import redis

import app.cache as cache


class FakePubSub:
    """Scripted stand-in for a redis-py PubSub object."""

    def __init__(self, messages=()):
        self._messages = list(messages)
        self.subscribed = []
        self.closed = False

    def subscribe(self, channel):
        self.subscribed.append(channel)

    def get_message(self, timeout=None):
        if self._messages:
            return {"type": "message", "data": self._messages.pop(0)}
        time.sleep(0.01)
        return None

    def close(self):
        self.closed = True


class FakeBusClient:
    """In-memory stand-in for a Redis client with pub/sub support."""

    def __init__(self, messages=()):
        self.published = []
        self._pubsub = FakePubSub(messages)
        self.closed = False
        self.publish_failures = 0
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value

    def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)

    def scan(self, cursor=0, match=None, count=500):
        prefix = (match or "").replace("*", "")
        return 0, [k for k in self.data if k.startswith(prefix)]

    def publish(self, channel, message):
        if self.publish_failures:
            raise redis.ConnectionError("redis away")
        self.published.append((channel, message))
        return 1

    def pubsub(self, **kwargs):
        return self._pubsub

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_cache_state():
    cache._l1.clear()
    cache._inflight.clear()
    cache._l2 = None
    cache._l2_pool = None
    cache._l2_unavailable = False
    yield
    cache.stop_invalidation_listener()
    cache._l1.clear()
    cache._inflight.clear()
    cache._l2 = None
    cache._l2_pool = None
    cache._l2_unavailable = False


@pytest.fixture()
def sync_bus(monkeypatch):
    """Run fire-and-forget writes inline against a FakeBusClient."""
    fake = FakeBusClient()
    monkeypatch.setattr(cache, "get_l2", lambda: fake)
    monkeypatch.setattr(cache, "_l2_fire_and_forget", lambda fn: cache._l2_safe(fn))
    return fake


def test_handle_del_drops_exact_key():
    cache._l1_set("url:1", {"id": 1}, 300)
    cache._l1_set("url:2", {"id": 2}, 300)
    cache._handle_invalidation_message("del url:1")
    assert cache._l1_get("url:1") == (None, False)
    assert cache._l1_get("url:2")[0] == {"id": 2}


def test_handle_clear_drops_prefix():
    cache._l1_set("url:1", {"id": 1}, 300)
    cache._l1_set("user:9", {"id": 9}, 300)
    cache._handle_invalidation_message("clear url:")
    assert cache._l1_get("url:1") == (None, False)
    assert cache._l1_get("user:9")[0] == {"id": 9}


def test_handle_bytes_and_garbage_ignored():
    cache._l1_set("url:1", {"id": 1}, 300)
    for bad in (b"del url:1", "", "nonsense", "del", "frobnicate url:1", None, 12345):
        cache._handle_invalidation_message(bad)
    # Bytes form is valid and applied; everything else ignored.
    assert cache._l1_get("url:1") == (None, False)


def test_mutators_broadcast_drops(sync_bus):
    cache.set_user(7, {"id": 7})
    cache.delete_user(7)
    cache.set_url(1, {"id": 1})
    cache.delete_url(1)
    cache.set_url_by_short_code("abc123", {"id": 1})
    cache.delete_url_by_short_code("abc123")
    cache.clear_all_users()
    cache.clear_all_urls()
    cache.clear_list_cache("list:urls:")

    messages = [m for _, m in sync_bus.published]
    assert "del user:7" in messages
    assert "del url:1" in messages
    assert "del short_code:abc123" in messages
    assert "clear user:" in messages
    assert "clear url:" in messages
    assert "clear list:urls:" in messages
    assert all(c == cache._INVALIDATE_CHANNEL for c, _ in sync_bus.published)


def test_broadcast_fail_open_when_redis_down(sync_bus):
    sync_bus.publish_failures = 1
    # Publish raises inside _l2_safe and is swallowed; local write stands.
    cache.set_url(1, {"id": 1})
    assert cache._l1_get("url:1")[0] == {"id": 1}


def test_subscriber_loop_applies_messages():
    cache._l1_set("url:5", {"id": 5}, 300)
    client = FakeBusClient(messages=["del url:5"])
    stop = threading.Event()
    thread = threading.Thread(
        target=cache._invalidation_loop, args=(stop,), kwargs={"client_factory": lambda: client}
    )
    thread.start()
    deadline = time.time() + 5.0
    while cache._l1_get("url:5")[0] is not None and time.time() < deadline:
        time.sleep(0.02)
    stop.set()
    thread.join(timeout=5.0)
    assert cache._l1_get("url:5") == (None, False)
    assert not thread.is_alive()
    assert client._pubsub.subscribed == [cache._INVALIDATE_CHANNEL]


def test_start_stop_idempotent(monkeypatch):
    monkeypatch.setattr(
        cache, "_create_redis_client", lambda: (_ for _ in ()).throw(ConnectionError("down"))
    )
    cache.start_invalidation_listener()
    first = cache._listener_thread
    assert first is not None and first.is_alive()
    cache.start_invalidation_listener()
    assert cache._listener_thread is first
    cache.stop_invalidation_listener()
    assert cache._listener_thread is None
    assert not first.is_alive()
    # Stopping when not running is a no-op.
    cache.stop_invalidation_listener()
