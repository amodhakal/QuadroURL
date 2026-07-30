import json
from concurrent.futures import ThreadPoolExecutor, wait

from app.models.event import Event


_event_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="event-writer")
_pending_futures = []


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
    try:
        Event.create(
            url_id=url_id,
            user_id=user_id,
            event_type=event_type,
            details=json.dumps(details_dict),
        )
    except Exception:
        pass
