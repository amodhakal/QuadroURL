"""Per-request identity helpers: client IP + request ID propagation (#106, #173)."""

import os
import uuid

from flask import g, has_request_context, request


def get_client_ip() -> str:
    """Best-effort client IP that doesn't blindly trust X-Forwarded-For.

    ``X-Forwarded-For`` is client-controlled and trivially spoofable. Nginx
    overwrites ``X-Real-IP`` with ``$remote_addr`` (see nginx.conf), so it is
    preferred. ``X-Forwarded-For`` is only honored when ``TRUST_PROXY_XFF``
    is explicitly enabled (single trusted proxy that appends) — and even
    then only the last entry (added by our proxy) is used.
    """
    if not has_request_context():
        return "unknown"
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if real_ip:
        return real_ip.split(",")[0].strip() or "unknown"
    if os.environ.get("TRUST_PROXY_XFF", "false").lower() == "true":
        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                return parts[-1]
    return request.remote_addr or "unknown"


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
