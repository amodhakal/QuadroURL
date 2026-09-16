import json
import logging
import secrets
import string
import time
from datetime import datetime, timezone

from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DateTimeField,
    IntegerField,
    IntegrityError,
    Model,
)


logger = logging.getLogger("consumer.url_create")

PENDING_TTL = 3600


class Url(Model):
    id = AutoField()
    user_id = IntegerField()
    short_code = CharField(unique=True)
    original_url = CharField()
    title = CharField()
    is_active = BooleanField(default=True)
    # Idempotency key written by producers (#113). Mirrors app/models/url.py;
    # nullable for rows created before the column existed.
    request_id = CharField(null=True, unique=True)
    created_at = DateTimeField(default=lambda: datetime.now(timezone.utc))
    updated_at = DateTimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        database = None
        table_name = "url"


def generate_short_code(length=6):
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))


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
                        pending_results.append(
                            (
                                request_id,
                                {"status": "error", "error": "Missing required fields"},
                            )
                        )
                    continue

                url = None
                deduplicated = False
                for attempt in range(5):
                    short_code = generate_short_code()
                    try:
                        # One savepoint per attempt: a failed INSERT must not
                        # poison the batch transaction (Postgres aborts it),
                        # and the request_id lookup below needs a usable
                        # transaction (#113).
                        with db.savepoint():
                            url = Url.create(
                                user_id=user_id,
                                short_code=short_code,
                                original_url=original_url,
                                title=title,
                                is_active=True,
                                request_id=request_id,
                            )
                        break
                    except IntegrityError:
                        # Request-id clash = redelivered retry of an
                        # already-stored row: reuse it so the retrying client
                        # gets its ready-status (#113). Anything else is a
                        # short-code clash, so try the next code.
                        try:
                            with db.savepoint():
                                url = Url.select().where(Url.request_id == request_id).get()
                            deduplicated = True
                            break
                        except Url.DoesNotExist:
                            continue
                        except Exception:
                            continue
                    except Exception:
                        continue

                if url is None:
                    logger.error(f"Failed to generate short code for request_id={request_id}")
                    pending_results.append(
                        (
                            request_id,
                            {
                                "status": "error",
                                "error": "Failed to generate unique short code",
                            },
                        )
                    )
                    continue

                pending_results.append(
                    (
                        request_id,
                        {
                            "status": "ready",
                            "id": url.id,
                            "short_code": url.short_code,
                            "original_url": url.original_url,
                            "title": url.title,
                        },
                    )
                )
                if deduplicated:
                    # The first attempt already queued the "created" event;
                    # only the ready-status is (re)written so the retrying
                    # client unblocks without a duplicate row or event (#113).
                    continue
                created_events.append(
                    {
                        "url_id": url.id,
                        "user_id": url.user_id,
                        "event_type": "created",
                        "details": {
                            "short_code": url.short_code,
                            "original_url": url.original_url,
                        },
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
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
