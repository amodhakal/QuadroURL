import json
import logging
import random
import string
import time
from datetime import datetime, timezone

import redis
from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DateTimeField,
    IntegerField,
    Model,
    TextField,
)
from playhouse.pool import PooledPostgresqlDatabase

import config

logger = logging.getLogger("consumer.url_create")

PENDING_TTL = 3600


class Url(Model):
    id = AutoField()
    user_id = IntegerField()
    short_code = CharField(unique=True)
    original_url = CharField()
    title = CharField()
    is_active = BooleanField(default=True)
    created_at = DateTimeField()
    updated_at = DateTimeField()

    class Meta:
        database = None
        table_name = "url"


def generate_short_code(length=6):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def _validate(data):
    request_id = data.get("request_id")
    user_id = data.get("user_id")
    original_url = data.get("original_url")
    title = data.get("title")
    return request_id, user_id, original_url, title


def handle_url_create_batch(messages, db, redis_client):
    """Create URLs for a batch of messages in one transaction + one Redis pipeline.

    Returns ``(ok, events)``.  ``ok`` is True when the batch was fully persisted
    (offsets may be committed).  When ``ok`` is False the caller keeps the buffer
    and retries.  ``events`` holds "created" event payloads for the created URLs.
    """
    if not messages:
        return True, []

    start = time.time()
    pending_results = []
    created_events = []
    created_count = 0

    try:
        db.connect(reuse_if_open=True)
        with db.atomic():
            for data in messages:
                request_id, user_id, original_url, title = _validate(data)

                if not all([request_id, user_id, original_url, title]):
                    logger.warning(f"Invalid url-create message: {data}")
                    if request_id:
                        pending_results.append((
                            request_id,
                            {"status": "error", "error": "Missing required fields"},
                        ))
                    continue

                url = None
                for attempt in range(5):
                    short_code = generate_short_code()
                    try:
                        url = Url.create(
                            user_id=user_id,
                            short_code=short_code,
                            original_url=original_url,
                            title=title,
                            is_active=True,
                        )
                        break
                    except Exception:
                        continue

                if url is None:
                    logger.error(
                        f"Failed to generate short code for request_id={request_id}"
                    )
                    pending_results.append((
                        request_id,
                        {
                            "status": "error",
                            "error": "Failed to generate unique short code",
                        },
                    ))
                    continue

                pending_results.append((
                    request_id,
                    {
                        "status": "ready",
                        "id": url.id,
                        "short_code": url.short_code,
                        "original_url": url.original_url,
                        "title": url.title,
                    },
                ))
                created_events.append({
                    "url_id": url.id,
                    "user_id": url.user_id,
                    "event_type": "created",
                    "details": {
                        "short_code": url.short_code,
                        "original_url": url.original_url,
                    },
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                created_count += 1
    except Exception:
        logger.exception("Failed to process url-create batch")
        return False, []

    if pending_results:
        try:
            pipe = redis_client.pipeline()
            for request_id, payload in pending_results:
                pipe.setex(f"url-pending:{request_id}", PENDING_TTL, json.dumps(payload))
            pipe.execute()
        except Exception:
            logger.exception("Failed to write url-pending keys to Redis")
            return False, []

    elapsed = time.time() - start
    logger.info(
        f"[url-creates] Processed {len(messages)} messages "
        f"({created_count} created) in {elapsed:.2f}s"
    )
    return True, created_events