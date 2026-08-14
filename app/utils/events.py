import json
import logging
from datetime import datetime, timezone

from app.utils.kafka_producer import publish_event

logger = logging.getLogger("quadroPE.events")


def create_event_async(url_id, user_id, event_type, details_dict):
    publish_event({
        "url_id": url_id,
        "user_id": user_id,
        "event_type": event_type,
        "details": details_dict,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def create_event(url_id, user_id, event_type, details_dict):
    create_event_async(url_id, user_id, event_type, details_dict)


def flush_events():
    from app.utils.kafka_producer import flush_producer
    flush_producer()
