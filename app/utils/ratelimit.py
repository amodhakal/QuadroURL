import functools
import logging
import threading
import time
import math
from flask import g, jsonify, request, make_response
from app.cache import get_l2

logger = logging.getLogger("quadroPE.ratelimit")

TOKEN_BUCKET_SCRIPT = """
local tokens_key = KEYS[1]
local timestamp_key = KEYS[2]

local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local time_res = redis.call('TIME')
local now = tonumber(time_res[1]) + tonumber(time_res[2]) / 1000000.0

local last_tokens = tonumber(redis.call('get', tokens_key))
local last_refilled = tonumber(redis.call('get', timestamp_key))

local current_tokens = capacity
if last_tokens ~= nil and last_refilled ~= nil then
    local delta = math.max(0.0, now - last_refilled)
    current_tokens = math.min(capacity, last_tokens + delta * refill_rate)
else
    last_refilled = now
end

if current_tokens >= requested then
    current_tokens = current_tokens - requested
    redis.call('set', tokens_key, current_tokens, 'EX', ttl)
    redis.call('set', timestamp_key, now, 'EX', ttl)
    return {1, current_tokens}
else
    redis.call('set', tokens_key, current_tokens, 'EX', ttl)
    redis.call('set', timestamp_key, now, 'EX', ttl)
    return {0, current_tokens}
end
"""

_script_obj = None


def get_script(client):
    global _script_obj
    if _script_obj is None:
        _script_obj = client.register_script(TOKEN_BUCKET_SCRIPT)
    return _script_obj


class LocalTokenBucket:
    """Thread-safe in-process token bucket used when Redis is unavailable.

    Same semantics as the Redis Lua script (capacity/refill_rate, one token
    per request), but per-process state: it keeps limits enforced during a
    Redis outage instead of failing every request open. It is a safety net,
    not a replacement for the shared bucket (#300).
    """

    def __init__(self, capacity, refill_rate, ttl=3600):
        capacity = float(capacity)
        refill_rate = float(refill_rate)
        if not math.isfinite(capacity) or capacity <= 0:
            raise ValueError("capacity must be a positive number")
        if not math.isfinite(refill_rate) or refill_rate < 0:
            raise ValueError("refill_rate must be a non-negative number")
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.ttl = float(ttl)
        self._buckets = {}
        self._lock = threading.Lock()

    def consume(self, identifier, now=None):
        """Take one token for ``identifier``; return ``(allowed, remaining)``."""
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, updated = self._buckets.get(identifier, (self.capacity, now))
            if updated > now:  # clock went backwards
                updated = now
            tokens = min(self.capacity, tokens + (now - updated) * self.refill_rate)
            allowed = tokens >= 1
            if allowed:
                tokens -= 1
            self._buckets[identifier] = (tokens, now)
            if len(self._buckets) > 1024:
                self._evict(now)
            return allowed, tokens

    def _evict(self, now):
        """Drop buckets untouched for longer than the key TTL (bounded memory)."""
        stale = [k for k, (_, updated) in self._buckets.items() if now - updated > self.ttl]
        for key in stale:
            self._buckets.pop(key, None)


_local_buckets = {}
_local_buckets_lock = threading.Lock()


def get_local_bucket(capacity, refill_rate, ttl=3600):
    """Return the process-local bucket for these settings, or None if unusable."""
    signature = (float(capacity), float(refill_rate), float(ttl))
    with _local_buckets_lock:
        bucket = _local_buckets.get(signature)
        if bucket is None:
            try:
                bucket = LocalTokenBucket(capacity, refill_rate, ttl)
            except (TypeError, ValueError):
                return None
            _local_buckets[signature] = bucket
    return bucket


def reset_local_buckets():
    """Drop all process-local buckets (tests / operational reset)."""
    with _local_buckets_lock:
        _local_buckets.clear()


_log_throttle = {}
_log_throttle_lock = threading.Lock()
LOG_THROTTLE_SECONDS = 60.0


def _warn_throttled(message, throttle_key):
    """Warn at most once per throttle window so an outage cannot flood logs."""
    now = time.monotonic()
    with _log_throttle_lock:
        last = _log_throttle.get(throttle_key)
        if last is not None and now - last < LOG_THROTTLE_SECONDS:
            return
        _log_throttle[throttle_key] = now
    logger.warning(message)


def default_key_func():
    from app.utils.request_ctx import get_client_ip

    if not hasattr(g, "current_user_id"):
        try:
            from app.utils.auth import authenticate_request

            authenticated = authenticate_request()
            if authenticated is not None and authenticated[0] is not None:
                record, method = authenticated
                g.api_key_id = record.id
                g.current_user_id = record.user_id
                g.auth_method = method
        except Exception:
            pass
    actor = (
        f"user:{g.current_user_id}" if hasattr(g, "current_user_id") else f"ip:{get_client_ip()}"
    )
    endpoint = (request.endpoint or "unmatched").replace("_v1.", ".")
    return f"{actor}:{endpoint}"


def rate_limit(
    capacity=10,
    refill_rate=1.0,
    key_func=default_key_func,
    ttl=3600,
    fail_closed=True,
):
    """
    Distributed Token Bucket Rate Limiting decorator using Redis Lua script.

    Fails closed by default: when Redis is unavailable the request is still
    rate limited by a process-local token bucket rather than being allowed
    through, and only a 503 is returned if that fallback cannot be built.
    Disabled entirely with RATELIMIT_ENABLED=false (e.g. load tests) and
    bypassed under app TESTING so the suite never 429s itself.

    :param capacity: Maximum tokens in bucket (max burst)
    :param refill_rate: Tokens added per second
    :param key_func: Callable that returns a unique string identifier for the rate limit bucket
    :param ttl: Redis key TTL in seconds
    :param fail_closed: Enforce limits (locally) instead of allowing requests
        through when Redis is unavailable
    """

    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            import os as _os

            if _os.environ.get("RATELIMIT_ENABLED", "true").lower() != "true":
                return f(*args, **kwargs)
            try:
                from flask import current_app as _ca, has_app_context as _hac

                bypass = (
                    _hac()
                    and _ca.config.get("TESTING")
                    and not _ca.config.get("RATELIMIT_IN_TESTS")
                )
            except Exception:
                bypass = False
            # Never catch exceptions from the wrapped handler and invoke it twice.
            if bypass:
                return f(*args, **kwargs)

            def unavailable():
                if not fail_closed:
                    _warn_throttled(
                        "Rate limiter unavailable and fail_closed is disabled; "
                        "allowing request without rate limiting",
                        "fail-open",
                    )
                    return f(*args, **kwargs)
                response = jsonify({"error": "Rate limiter unavailable"})
                response.status_code = 503
                response.headers["Retry-After"] = "1"
                return response

            def local_fallback(identifier):
                """Enforce the limit in-process while Redis is unavailable."""
                bucket = get_local_bucket(capacity, refill_rate, ttl)
                if bucket is None:
                    logger.error("Local rate limit fallback unavailable; returning 503")
                    return None
                _warn_throttled(
                    "Rate limiter backend unavailable; enforcing limits with the "
                    "in-process fallback bucket (not shared across workers)",
                    "local-fallback",
                )
                try:
                    allowed, remaining = bucket.consume(identifier)
                except Exception:
                    logger.exception("Local rate limit fallback failed")
                    return None
                return allowed, remaining

            try:
                client = get_l2()
            except Exception:
                logger.exception("Rate limiter connection unavailable")
                client = None

            identifier = key_func()
            tokens_key = f"ratelimit:{identifier}:tokens"
            timestamp_key = f"ratelimit:{identifier}:timestamp"

            allowed = None
            remaining = None

            if client is not None:
                try:
                    script = get_script(client)
                    result = script(
                        keys=[tokens_key, timestamp_key],
                        args=[capacity, refill_rate, 1, ttl],
                    )
                    allowed = int(result[0])
                    remaining = float(result[1])
                except Exception as e:
                    logger.error(f"Rate limiter error: {e}")
                    allowed = None
                    remaining = None
                    client = None

            if allowed is None:
                # Redis path unusable: keep enforcing the limit locally when
                # failing closed, otherwise let the request through.
                fallback_result = local_fallback(identifier) if fail_closed else None
                if fallback_result is None:
                    return unavailable()
                allowed, remaining = fallback_result

            retry_after = 0
            if not allowed and refill_rate > 0:
                deficit = 1 - remaining
                retry_after = max(1, math.ceil(deficit / refill_rate))

            headers = {
                "X-RateLimit-Limit": str(capacity),
                "X-RateLimit-Remaining": str(max(0, int(remaining))),
                "X-RateLimit-Reset": str(int(time.time() + retry_after)),
            }

            if not allowed:
                _warn_throttled(
                    "Rate limit exceeded and request rejected",
                    f"rate-limited:{request.endpoint or 'unmatched'}",
                )
                response = jsonify({"error": "Rate limit exceeded", "retry_after": retry_after})
                response.status_code = 429
                response.headers.update(headers)
                response.headers["Retry-After"] = str(retry_after)
                return response

            response = make_response(f(*args, **kwargs))
            response.headers.update(headers)
            return response

        return wrapper

    return decorator


_warned_disabled = False


def warn_if_disabled():
    """Startup warning when rate limiting is switched off entirely."""
    global _warned_disabled
    if _warned_disabled:
        return
    _warned_disabled = True
    import os as _os

    if _os.environ.get("RATELIMIT_ENABLED", "true").lower() != "true":
        logger.warning(
            "RATELIMIT_ENABLED is false: all rate limits are disabled. "
            "Enable it before exposing the app to untrusted traffic."
        )
