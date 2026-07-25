import functools
import logging
import time
from flask import jsonify, request
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
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    return f"{ip}:{request.endpoint or request.path}"

def rate_limit(capacity=10, refill_rate=1.0, key_func=default_key_func, ttl=3600):
    """
    Distributed Token Bucket Rate Limiting decorator using Redis Lua script.
    
    :param capacity: Maximum tokens in bucket (max burst)
    :param refill_rate: Tokens added per second
    :param key_func: Callable that returns a unique string identifier for the rate limit bucket
    :param ttl: Redis key TTL in seconds
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            client = get_l2()
            if client is None:
                # Fail open if Redis is unavailable
                return f(*args, **kwargs)

            identifier = key_func()
            tokens_key = f"ratelimit:{identifier}:tokens"
            timestamp_key = f"ratelimit:{identifier}:timestamp"

            try:
                script = get_script(client)
                result = script(keys=[tokens_key, timestamp_key], args=[capacity, refill_rate, 1, ttl])
                allowed = int(result[0])
                remaining = float(result[1])
            except Exception as e:
                logger.error(f"Rate limiter error: {e}")
                # Fail open on Redis errors
                return f(*args, **kwargs)

            retry_after = 0
            if not allowed and refill_rate > 0:
                deficit = 1 - remaining
                retry_after = max(1, int(deficit / refill_rate))

            headers = {
                "X-RateLimit-Limit": str(capacity),
                "X-RateLimit-Remaining": str(max(0, int(remaining))),
                "X-RateLimit-Reset": str(int(time.time() + retry_after))
            }

            if not allowed:
                response = jsonify({"error": "Rate limit exceeded", "retry_after": retry_after})
                response.status_code = 429
                response.headers.update(headers)
                response.headers["Retry-After"] = str(retry_after)
                return response

            response = f(*args, **kwargs)
            if hasattr(response, "headers"):
                response.headers.update(headers)
            return response

        return wrapper
    return decorator
