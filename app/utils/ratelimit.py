import functools
import logging
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
    fail_closed=False,
):
    """
    Distributed Token Bucket Rate Limiting decorator using Redis Lua script.

    Generous by default and fail-open: disabled entirely with
    RATELIMIT_ENABLED=false (e.g. load tests) and bypassed under
    app TESTING so the suite never 429s itself.

    :param capacity: Maximum tokens in bucket (max burst)
    :param refill_rate: Tokens added per second
    :param key_func: Callable that returns a unique string identifier for the rate limit bucket
    :param ttl: Redis key TTL in seconds
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
                    return f(*args, **kwargs)
                response = jsonify({"error": "Rate limiter unavailable"})
                response.status_code = 503
                response.headers["Retry-After"] = "1"
                return response

            try:
                client = get_l2()
            except Exception:
                logger.exception("Rate limiter connection unavailable")
                return unavailable()
            if client is None:
                return unavailable()

            identifier = key_func()
            tokens_key = f"ratelimit:{identifier}:tokens"
            timestamp_key = f"ratelimit:{identifier}:timestamp"

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
                return unavailable()

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
