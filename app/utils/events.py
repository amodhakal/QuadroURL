import logging
from datetime import datetime, timezone

from app.utils.kafka_producer import publish_event

logger = logging.getLogger("quadroPE.events")


def create_event(url_id, user_id, event_type, details_dict):
    try:
        from app.utils.request_ctx import get_request_id

        request_id = get_request_id()
    except Exception:
        request_id = ""
    publish_event(
        {
            "url_id": url_id,
            "user_id": user_id,
            "event_type": event_type,
            "details": details_dict,
            "request_id": request_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def flush_events():
    from app.utils.kafka_producer import flush_producer

    flush_producer()
