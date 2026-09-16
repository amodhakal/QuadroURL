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

5. **Cross-worker invalidation bus** — L1 is per-process, so a mutation on
   one Gunicorn worker is broadcast as a drop directive on a Redis pub/sub
   channel; every worker's subscriber thread applies drops to its local L1.
   Only drops are broadcast (never fills): peers fall through to L2, which
   the mutating worker just wrote, so there is no cross-worker herd (#118).
   Theoretical race: the L2 write and the publish are both async, so a peer
   refetch landing exactly between them can re-cache the pre-mutation value
   from L2 with a fresh TTL. The window is executor lag (microseconds to low
   milliseconds) versus the up-to-TTL staleness this bus eliminates, and the
   outcome is still bounded by TTL exactly as before — accepted.
"""

import json
import logging
import os
import random
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import redis

logger = logging.getLogger("quadroPE.cache")

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
_l2_unavailable_since = 0.0
# Cooldown before retrying Redis after a failure (lets Redis recover
# without an app restart). See #117.
_L2_RETRY_COOLDOWN_S = 30.0

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


def _create_redis_client():
    """Build a fresh Redis client; the caller owns closing it."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    pool = redis.ConnectionPool.from_url(
        redis_url,
        max_connections=25,
        socket_timeout=0.5,
        socket_connect_timeout=0.5,
        decode_responses=True,
    )
    client = redis.Redis(connection_pool=pool)
    client.ping()
    return client


def get_l2():
    """Return the shared Redis client, or None when Redis is unavailable.

    Fail-open: callers treat None as a cache miss and fall through to the
    database. A failed Redis stays sidelined for a short cooldown so a
    restart recovers without an app restart (see #117).
    """
    global _l2, _l2_unavailable, _l2_unavailable_since
    if _l2_unavailable:
        # Retry after cooldown so a Redis restart recovers automatically.
        if time.time() - _l2_unavailable_since < _L2_RETRY_COOLDOWN_S:
            return None
        _l2_unavailable = False
        _l2 = None
    if _l2 is None:
        try:
            _l2 = _create_redis_client()
            _l2_pool = _l2.connection_pool
        except (redis.ConnectionError, redis.TimeoutError, redis.RedisError):
            _l2_unavailable = True
            _l2_unavailable_since = time.time()
            _l2 = None
    return _l2


def _note_l2_failure():
    global _l2_unavailable, _l2_unavailable_since
    _l2_unavailable = True
    _l2_unavailable_since = time.time()


def _l2_safe(fn):
    """Run *fn* against L2, returning None on any Redis error.

    Fail-open: read failures degrade to a database fetch by the caller.
    """
    try:
        client = get_l2()
        if client is not None:
            return fn(client)
    except redis.RedisError:
        pass
    return None


def _l2_fire_and_forget(fn):
    """Best-effort async L2 write.

    Fail-open: write failures are swallowed; L1 remains authoritative.
    """
    try:
        _executor.submit(lambda: _l2_safe(fn))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cross-worker invalidation bus (#118)
# ---------------------------------------------------------------------------

_INVALIDATE_CHANNEL = "cache:invalidate"

_listener_thread = None
_listener_lock = threading.Lock()
_listener_stop = threading.Event()


def _broadcast_invalidate(op, target):
    """Publish an L1 drop directive for the other workers (best-effort).

    Fail-open like the rest of this module: when Redis is down the publish
    is swallowed and workers behave exactly as before (staleness ≤ TTL).
    """

    def _pub(client):
        client.publish(_INVALIDATE_CHANNEL, f"{op} {target}")

    _l2_fire_and_forget(_pub)


def _handle_invalidation_message(data):
    """Apply one bus message to the local L1; ignore garbage."""
    try:
        text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data
        if not isinstance(text, str):
            return
        op, _, target = text.partition(" ")
        if op == "del" and target:
            _l1_delete(target)
        elif op == "clear" and target:
            _l1_clear(target)
    except Exception:
        logger.exception("Cache invalidation message failed")


def _invalidation_loop(stop, client_factory=None):
    """Subscribe to the bus until *stop* is set (own thread, own connection)."""
    factory = client_factory or _create_redis_client
    while not stop.is_set():
        try:
            client = factory()
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            try:
                pubsub.subscribe(_INVALIDATE_CHANNEL)
                while not stop.is_set():
                    message = pubsub.get_message(timeout=1.0)
                    if message and message.get("type") == "message":
                        _handle_invalidation_message(message.get("data"))
            finally:
                for closable in (pubsub, client):
                    try:
                        closable.close()
                    except Exception:
                        pass
        except Exception:
            logger.exception("Cache invalidation subscriber failed")
        if not stop.is_set():
            # Reconnect backoff — fail-open while Redis is away (#117).
            stop.wait(5.0)


def start_invalidation_listener():
    """Start the cross-worker invalidation subscriber (idempotent).

    Called from lifecycle.start_background_workers, never from import or
    create_app, so tests stay thread-free unless they opt in.
    """
    global _listener_thread
    with _listener_lock:
        if _listener_thread is not None and _listener_thread.is_alive():
            return
        _listener_stop.clear()
        _listener_thread = threading.Thread(
            target=_invalidation_loop,
            args=(_listener_stop,),
            name="cache-invalidate",
            daemon=True,
        )
        _listener_thread.start()


def stop_invalidation_listener(timeout=5.0):
    """Stop the subscriber; no-op when not running (tests / shutdown)."""
    global _listener_thread
    with _listener_lock:
        thread = _listener_thread
        _listener_thread = None
    if thread is not None:
        _listener_stop.set()
        thread.join(timeout=timeout)


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
                _l2_fire_and_forget(lambda client, k=key, p=payload, t=ttl: client.setex(k, t, p))
            return value
        finally:
            event.set()
            _clear_inflight_event(key)
    else:
        # Concurrent caller — wait for the primary to finish, then read from cache.
        # If the primary errored or the entry was evicted, fall back to a
        # direct fetch instead of reporting a false 404 (#116).
        waited = event.wait(timeout=30)
        cached, _ = _l1_get(key)
        if cached is not None:
            if cached is _NEGATIVE_SENTINEL:
                return None
            return cached
        if not waited:
            # Primary timed out; do a direct fetch rather than misreporting.
            try:
                return fetch_fn()
            except Exception:
                return None
        # Primary finished but cache is empty (error path) — try once directly.
        try:
            value = fetch_fn()
        except Exception:
            return None
        return value


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
                _l2_fire_and_forget(lambda client, k=key, p=payload, t=ttl: client.setex(k, t, p))
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
    _broadcast_invalidate("del", key)


def delete_user(user_id):
    key = f"user:{user_id}"
    _l1_delete(key)
    _l2_fire_and_forget(lambda client: client.delete(key))
    _broadcast_invalidate("del", key)


def _scan_delete(client, pattern):
    """Delete keys matching *pattern* via SCAN (non-blocking, #119)."""
    cursor = 0
    batch = []
    while True:
        cursor, keys = client.scan(cursor=cursor, match=pattern, count=500)
        batch.extend(keys)
        if len(batch) >= 500:
            client.delete(*batch)
            batch = []
        if cursor == 0:
            break
    if batch:
        client.delete(*batch)


def clear_all_users():
    _l1_clear("user:")
    _l2_fire_and_forget(lambda client: _scan_delete(client, "user:*"))
    _broadcast_invalidate("clear", "user:")


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
    _broadcast_invalidate("del", key)


def delete_url(url_id):
    key = f"url:{url_id}"
    _l1_delete(key)
    _l2_fire_and_forget(lambda client: client.delete(key))
    _broadcast_invalidate("del", key)


def clear_all_urls():
    _l1_clear("url:")
    _l2_fire_and_forget(lambda client: _scan_delete(client, "url:*"))
    _broadcast_invalidate("clear", "url:")


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
    _broadcast_invalidate("del", key)


def delete_url_by_short_code(short_code):
    key = f"short_code:{short_code}"
    _l1_delete(key)
    _l2_fire_and_forget(lambda client: client.delete(key))
    _broadcast_invalidate("del", key)


# ---------------------------------------------------------------------------
# List response cache (short TTL, L1 only)
# ---------------------------------------------------------------------------

# List endpoints (GET /users, /urls, /events) are hot under load but their
# results change on writes.  A short L1 TTL keeps them off Postgres almost
# entirely while bounding staleness to a few seconds.
_LIST_TTL = 5


def get_list_cache(key):
    value, _ = _l1_get(key)
    if value is not None:
        return value
    return None


def set_list_cache(key, value, ttl=_LIST_TTL):
    _l1_set(key, value, ttl)


def clear_list_cache(pattern):
    _l1_clear(pattern)
    _broadcast_invalidate("clear", pattern)
