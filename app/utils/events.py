import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, wait

from peewee import IntegrityError

from app.models.event import Event

logger = logging.getLogger("quadroPE.events")

_event_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="event-writer")
_pending_futures = []

_MAX_RETRIES = 5


def create_event_async(url_id, user_id, event_type, details_dict):
    future = _event_executor.submit(
        _do_create_event, url_id, user_id, event_type, details_dict
    )
    _pending_futures.append(future)


def create_event(url_id, user_id, event_type, details_dict):
    _do_create_event(url_id, user_id, event_type, details_dict)


def flush_events():
    if _pending_futures:
        wait(_pending_futures)
        _pending_futures.clear()


def _do_create_event(url_id, user_id, event_type, details_dict):
    details = json.dumps(details_dict)
    for attempt in range(_MAX_RETRIES):
        try:
            Event.create(
                url_id=url_id,
                user_id=user_id,
                event_type=event_type,
                details=details,
            )
            return
        except IntegrityError:
            # The referenced row (e.g. a freshly created URL) may not be committed
            # yet: the event writer runs on its own connection, potentially before
            # the request transaction has committed. Back off briefly and retry.
            time.sleep(0.01 * (attempt + 1))
    logger.error(
        "Failed to persist event type=%s url_id=%s after %d attempts",
        event_type,
        url_id,
        _MAX_RETRIES,
    )
