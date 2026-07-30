"""Redis-backed L1/L2 cache with cache-stampede (thundering herd) prevention.

The cache layer implements four complementary anti-stampede strategies:

1. **Single-flight deduplication** — When a cache miss occurs, only the first
   request computes the value and populates the cache.  Subsequent concurrent
   requests for the same key wait on an in-process ``threading.Event`` and then
   read the freshly populated cache, rather than each hitting the database.

2. **Probabilistic early expiration** — Entries are considered "stale" at a
   configurable fraction of their TTL (default 80 %).  When a stale entry is
   read, the *first* reader triggers a background refresh while still returning
   the stale value to the caller.  This avoids the hard-miss cascade entirely
   for hot keys.

3. **Negative caching** — Keys that do not exist in the database are cached with
   a short TTL (default 10 s) so that repeated lookups for the same missing key
   do not hammer the database.

4. **L1 TTL jitter** — A small random jitter is added to every L1 TTL so that
   entries across the 6 Gunicorn replicas do not expire in lock-step.
"""

import json
import os
import random
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import redis

# ---------------------------------------------------------------------------
# L1 in-process cache (OrderedDict, LRU eviction)
# ---------------------------------------------------------------------------

_l1 = OrderedDict()
_l1_lock = threading.Lock()
_L1_MAX = 2048

# Fraction of TTL at which an entry is considered "stale" and eligible for
# background refresh.  0.8 means we start refreshing at 80 % of the TTL.
_EARLY_EXPIRY_FRACTION = 0.8

# Default TTL for negative-cache entries (keys that don't exist in the DB).
_NEGATIVE_CACHE_TTL = 10

# Sentinel stored in L1 to distinguish "cached None (negative cache)" from
# "not in cache at all".
_NEGATIVE_SENTINEL = "__NEGATIVE_CACHE__"

# ---------------------------------------------------------------------------
# L2 Redis connection
# ---------------------------------------------------------------------------

_l2 = None
_l2_pool = None
_l2_unavailable = False

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cache-writer")

# ---------------------------------------------------------------------------
# Single-flight bookkeeping (in-process)
# ---------------------------------------------------------------------------

# Maps cache-key -> threading.Event.  When a miss occurs the first caller
# creates an Event, does the DB work, populates the cache, then sets the Event.
# Concurrent callers find the existing Event and wait on it.
_inflight = {}
_inflight_lock = threading.Lock()


# ---------------------------------------------------------------------------
# JSON encoder
# ---------------------------------------------------------------------------

class _Encoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


# ---------------------------------------------------------------------------
# L1 helpers
# ---------------------------------------------------------------------------

def _jitter_ttl(ttl):
    """Add +/- 10 % jitter to *ttl* to prevent synchronized L1 evictions."""
    if ttl <= 0:
        return ttl
    return ttl * (1.0 + random.uniform(-0.1, 0.1))


def _l1_get(key):
    """Return ``(value, is_stale)`` from L1, or ``(None, False)`` on miss.

    *is_stale* is True when the entry exists but has crossed the early-expiry
    threshold, signalling that a background refresh should be triggered.
    """
    with _l1_lock:
        if key not in _l1:
            return None, False
        value, expiry, created_at, original_ttl = _l1[key]
        now = time.time()
        if now > expiry:
            # Hard expiry — entry is gone.
            _l1.pop(key, None)
            return None, False
        _l1.move_to_end(key)
        # Check early-expiry threshold.
        is_stale = (now - created_at) > (original_ttl * _EARLY_EXPIRY_FRACTION)
        return value, is_stale


def _l1_set(key, value, ttl=300):
    with _l1_lock:
        if key in _l1:
            _l1.move_to_end(key)
        jittered = _jitter_ttl(ttl)
        now = time.time()
        _l1[key] = (value, now + jittered, now, ttl)
        while len(_l1) > _L1_MAX:
            _l1.popitem(last=False)


def _l1_delete(key):
    with _l1_lock:
        _l1.pop(key, None)


def _l1_clear(pattern):
    with _l1_lock:
        keys_to_delete = [k for k in _l1 if k.startswith(pattern)]
        for k in keys_to_delete:
            _l1.pop(k, None)


# ---------------------------------------------------------------------------
# L2 Redis helpers
# ---------------------------------------------------------------------------

def get_l2():
    global _l2, _l2_unavailable
    if _l2_unavailable:
        return None
    if _l2 is None:
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        try:
            _l2_pool = redis.ConnectionPool.from_url(
                redis_url,
                max_connections=25,
                socket_timeout=0.5,
                socket_connect_timeout=0.5,
                decode_responses=True,
            )
            _l2 = redis.Redis(connection_pool=_l2_pool)
            _l2.ping()
        except (redis.ConnectionError, redis.TimeoutError, redis.RedisError):
            _l2_unavailable = True
            _l2 = None
    return _l2


def _l2_safe(fn):
    try:
        client = get_l2()
        if client is not None:
            return fn(client)
    except redis.RedisError:
        pass
    return None


def _l2_fire_and_forget(fn):
    try:
        _executor.submit(lambda: _l2_safe(fn))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Single-flight helper
# ---------------------------------------------------------------------------

def _acquire_inflight(key):
    """Atomically get-or-create the Event for *key*.

    Returns ``(event, is_primary)``.  *is_primary* is True only for the first
    caller that creates the Event; all concurrent callers receive
    ``is_primary=False`` and should wait on the Event.
    """
    with _inflight_lock:
        if key not in _inflight:
            _inflight[key] = threading.Event()
            return _inflight[key], True
        return _inflight[key], False

def _clear_inflight_event(key):
    with _inflight_lock:
        _inflight.pop(key, None)


# ---------------------------------------------------------------------------
# Generic cache-miss resolution with single-flight + background refresh
# ---------------------------------------------------------------------------

def _resolve_miss(key, fetch_fn, ttl, negative_ttl=None):
    """Resolve a cache miss for *key* using single-flight deduplication.

    :param key: Cache key (without namespace prefix).
    :param fetch_fn: Zero-arg callable that returns the value from the DB
                     (or ``None`` if the record doesn't exist).
    :param ttl: TTL for positive cache entries.
    :param negative_ttl: TTL for negative-cache entries.  Defaults to
                         ``_NEGATIVE_CACHE_TTL``.
    :returns: The resolved value, or ``None`` if the record doesn't exist.
    """
    if negative_ttl is None:
        negative_ttl = _NEGATIVE_CACHE_TTL

    # Check L1 first — a previous miss may have already populated the cache
    # (positive or negative sentinel).
    cached, _ = _l1_get(key)
    if cached is not None:
        if cached is _NEGATIVE_SENTINEL:
            return None
        return cached

    event, is_primary = _acquire_inflight(key)

    if is_primary:
        try:
            value = fetch_fn()
            if value is None:
                # Negative cache — store sentinel so subsequent lookups skip DB.
                _l1_set(key, _NEGATIVE_SENTINEL, negative_ttl)
                _l2_fire_and_forget(
                    lambda client, k=key, t=negative_ttl: client.setex(k, t, "null")
                )
            else:
                _l1_set(key, value, ttl)
                payload = json.dumps(value, cls=_Encoder)
                _l2_fire_and_forget(
                    lambda client, k=key, p=payload, t=ttl: client.setex(k, t, p)
                )
            return value
        finally:
            event.set()
            _clear_inflight_event(key)
    else:
        # Concurrent caller — wait for the primary to finish, then read from cache.
        event.wait(timeout=30)
        cached, _ = _l1_get(key)
        if cached is _NEGATIVE_SENTINEL:
            return None
        return cached


def _background_refresh(key, fetch_fn, ttl, negative_ttl=None):
    """Trigger a background refresh for a stale (but not expired) entry.

    Only the first caller wins the single-flight race; others return
    immediately without blocking.
    """
    if negative_ttl is None:
        negative_ttl = _NEGATIVE_CACHE_TTL

    event, is_primary = _acquire_inflight(key)
    if not is_primary:
        return  # Another refresh is already in progress.

    def _do_refresh():
        try:
            value = fetch_fn()
            if value is None:
                _l1_set(key, _NEGATIVE_SENTINEL, negative_ttl)
                _l2_fire_and_forget(
                    lambda client, k=key, t=negative_ttl: client.setex(k, t, "null")
                )
            else:
                _l1_set(key, value, ttl)
                payload = json.dumps(value, cls=_Encoder)
                _l2_fire_and_forget(
                    lambda client, k=key, p=payload, t=ttl: client.setex(k, t, p)
                )
        finally:
            event.set()
            _clear_inflight_event(key)

    _executor.submit(_do_refresh)


# ---------------------------------------------------------------------------
# User cache API
# ---------------------------------------------------------------------------

def get_user(user_id):
    key = f"user:{user_id}"
    value, is_stale = _l1_get(key)
    if value is not None:
        if value is _NEGATIVE_SENTINEL:
            return None
        if is_stale:
            _background_refresh(key, lambda: _fetch_user(user_id), 300)
        return value

    # Check L2.
    def _read(client):
        data = client.get(key)
        return data

    l2_data = _l2_safe(_read)
    if l2_data is not None:
        if l2_data == "null":
            # Negative cache hit in L2.
            _l1_set(key, _NEGATIVE_SENTINEL, _NEGATIVE_CACHE_TTL)
            return None
        value = json.loads(l2_data)
        _l1_set(key, value, 300)
        return value

    # Full miss — single-flight DB fetch.
    return _resolve_miss(key, lambda: _fetch_user(user_id), 300)


def _fetch_user(user_id):
    """Fetch a user from the database (called only on cache miss)."""
    from app.models.user import User
    from playhouse.shortcuts import model_to_dict

    try:
        user = User.get_by_id(user_id)
        return model_to_dict(user)
    except User.DoesNotExist:
        return None


def set_user(user_id, data, ttl=300):
    key = f"user:{user_id}"
    _l1_set(key, data, ttl)
    payload = json.dumps(data, cls=_Encoder)
    _l2_fire_and_forget(lambda client: client.setex(key, ttl, payload))


def delete_user(user_id):
    key = f"user:{user_id}"
    _l1_delete(key)
    _l2_fire_and_forget(lambda client: client.delete(key))


def clear_all_users():
    _l1_clear("user:")
    _l2_fire_and_forget(lambda client: client.delete(*client.keys("user:*")))


# ---------------------------------------------------------------------------
# URL cache API
# ---------------------------------------------------------------------------

def get_url(url_id):
    key = f"url:{url_id}"
    value, is_stale = _l1_get(key)
    if value is not None:
        if value is _NEGATIVE_SENTINEL:
            return None
        if is_stale:
            _background_refresh(key, lambda: _fetch_url(url_id), 300)
        return value

    def _read(client):
        data = client.get(key)
        return data

    l2_data = _l2_safe(_read)
    if l2_data is not None:
        if l2_data == "null":
            _l1_set(key, _NEGATIVE_SENTINEL, _NEGATIVE_CACHE_TTL)
            return None
        value = json.loads(l2_data)
        _l1_set(key, value, 300)
        return value

    return _resolve_miss(key, lambda: _fetch_url(url_id), 300)


def _fetch_url(url_id):
    """Fetch a URL from the database (called only on cache miss)."""
    from app.models.url import Url
    from playhouse.shortcuts import model_to_dict

    try:
        url = Url.get_by_id(url_id)
        data = model_to_dict(url, recurse=False)
        data["user_id"] = data.pop("user")
        return data
    except Url.DoesNotExist:
        return None


def set_url(url_id, data, ttl=300):
    key = f"url:{url_id}"
    _l1_set(key, data, ttl)
    payload = json.dumps(data, cls=_Encoder)
    _l2_fire_and_forget(lambda client: client.setex(key, ttl, payload))


def delete_url(url_id):
    key = f"url:{url_id}"
    _l1_delete(key)
    _l2_fire_and_forget(lambda client: client.delete(key))


def clear_all_urls():
    _l1_clear("url:")
    _l2_fire_and_forget(lambda client: client.delete(*client.keys("url:*")))


# ---------------------------------------------------------------------------
# Short-code URL cache API
# ---------------------------------------------------------------------------

def get_url_by_short_code(short_code):
    key = f"short_code:{short_code}"
    value, is_stale = _l1_get(key)
    if value is not None:
        if value is _NEGATIVE_SENTINEL:
            return None
        if is_stale:
            _background_refresh(key, lambda: _fetch_url_by_short_code(short_code), 300)
        return value

    def _read(client):
        data = client.get(key)
        return data

    l2_data = _l2_safe(_read)
    if l2_data is not None:
        if l2_data == "null":
            _l1_set(key, _NEGATIVE_SENTINEL, _NEGATIVE_CACHE_TTL)
            return None
        value = json.loads(l2_data)
        _l1_set(key, value, 300)
        return value

    return _resolve_miss(key, lambda: _fetch_url_by_short_code(short_code), 300)


def _fetch_url_by_short_code(short_code):
    from app.models.url import Url
    from playhouse.shortcuts import model_to_dict

    try:
        url = Url.select().where(Url.short_code == short_code).get()
        data = model_to_dict(url, recurse=False)
        data["user_id"] = data.pop("user")
        return data
    except Url.DoesNotExist:
        return None


def set_url_by_short_code(short_code, data, ttl=300):
    key = f"short_code:{short_code}"
    _l1_set(key, data, ttl)
    payload = json.dumps(data, cls=_Encoder)
    _l2_fire_and_forget(lambda client: client.setex(key, ttl, payload))


def delete_url_by_short_code(short_code):
    key = f"short_code:{short_code}"
    _l1_delete(key)
    _l2_fire_and_forget(lambda client: client.delete(key))
