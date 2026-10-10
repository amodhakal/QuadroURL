"""Per-request identity helpers: client IP + request ID propagation (#106, #173)."""

import uuid

from flask import g, has_request_context, request


def get_client_ip() -> str:
    if not has_request_context():
        return "unknown"
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
