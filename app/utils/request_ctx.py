"""Per-request identity helpers: client IP + request ID propagation (#106, #173)."""

import logging
import os
import uuid

from flask import g, has_request_context, request

logger = logging.getLogger("quadroPE.request_ctx")

UNKNOWN_IP = "unknown"

# Any of these being present means a reverse proxy is in front of the app,
# so TRUSTED_PROXY_COUNT=0 would make every client look like the proxy.
PROXY_ENV_HINTS = (
    "TRUSTED_PROXY_COUNT",
    "TRUST_PROXY_COUNT",
    "FORWARDED_ALLOW_IPS",
    "X_FORWARDED_FOR",
    "X_FORWARDED_PROTO",
    "USE_X_FORWARDED_HEADERS",
    "PROXY_COUNT",
)


def _trusted_proxy_count() -> int:
    """Number of trusted reverse proxies in front of the app (0 = none)."""
    raw = None
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            raw = current_app.config.get("TRUSTED_PROXY_COUNT")
    except Exception:
        raw = None
    if raw is None or raw == "":
        raw = os.environ.get("TRUSTED_PROXY_COUNT") or os.environ.get("TRUST_PROXY_COUNT")
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, count)


def get_client_ip() -> str:
    """Client IP, proxy-aware but only as far as we trust the chain (#300).

    With ``TRUSTED_PROXY_COUNT=0`` (the default) ``X-Forwarded-For`` is
    ignored entirely: it is client-supplied and would let any caller pick
    their own bucket key. When N proxies are trusted, the Nth address from
    the right of ``X-Forwarded-For`` is the last one a trusted hop appended,
    matching what ``ProxyFix(x_for=N)`` uses as the client address.
    """
    if not has_request_context():
        return UNKNOWN_IP
    remote_addr = request.remote_addr or UNKNOWN_IP

    trusted = _trusted_proxy_count()
    if trusted <= 0:
        return remote_addr

    forwarded = request.headers.get("X-Forwarded-For") or ""
    chain = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if len(chain) < trusted:
        # Fewer hops than expected: the chain is not fully populated, so the
        # rightmost entry could be spoofed. Fall back to the socket peer.
        return remote_addr
    return chain[-trusted]


def _is_production() -> bool:
    environment = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "")).lower()
    return environment in {"production", "prod"} or os.environ.get(
        "FLASK_DEBUG", ""
    ).lower() == "false"


_warned_untrusted_proxy = False


def warn_if_proxy_untrusted():
    """Startup warning: production behind a proxy with no trusted proxy count.

    Every user then shares the proxy's IP bucket, so per-IP limits (including
    password attempts) stop being per-user.
    """
    global _warned_untrusted_proxy
    if _warned_untrusted_proxy:
        return
    _warned_untrusted_proxy = True
    if not _is_production():
        return
    if _trusted_proxy_count() > 0:
        return
    if not any(os.environ.get(name) not in (None, "") for name in PROXY_ENV_HINTS):
        return
    logger.warning(
        "TRUSTED_PROXY_COUNT is 0 but the app is deployed behind a proxy: "
        "X-Forwarded-For is ignored, so all clients share one rate limit "
        "bucket. Set TRUSTED_PROXY_COUNT to the number of proxy hops."
    )


def get_request_id() -> str:
    """Return the request ID, creating one if needed.

    Honors an incoming ``X-Request-ID`` header so IDs propagate end-to-end
    (client -> app -> Kafka -> consumers); otherwise generates a short uuid.
    """
    if has_request_context():
        rid = getattr(g, "request_id", None)
        if rid:
            return rid
        incoming = (request.headers.get("X-Request-ID") or "").strip()
        rid = incoming[:64] if incoming else uuid.uuid4().hex[:16]
        g.request_id = rid
        return rid
    return uuid.uuid4().hex[:16]