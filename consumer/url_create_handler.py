import json
import logging
import secrets
import string
import time
from datetime import datetime, timezone

from peewee import DataError, IntegrityError
from models import Url


logger = logging.getLogger("consumer.url_create")

PENDING_TTL = 3600


def generate_short_code(length=6):
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))


def _validate(data):
    request_id = data.get("request_id")
    user_id = data.get("user_id")
    original_url = data.get("original_url")
    title = data.get("title")
    return request_id, user_id, original_url, title


def _parse_expires_at(value):
    """Tolerant ``expires_at`` coercion for queue payloads (#192).

    Returns an aware datetime, or None when absent/unparseable. Naive values
    are assumed UTC (storage is tz-naive TIMESTAMP). Never raises: invalid
    input yields None so one bad message cannot poison the batch. The API
    layer already rejects naive/past input, so this only guards malformed
    queue payloads. Standalone copy — the consumer cannot import ``app``.
    """
    if value is None or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _expires_at_iso(value):
    """Serialize a stored ``expires_at`` as ISO string or None (#192)."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return None


def handle_url_create_batch(messages, db, redis_client, *, raise_poison=False):
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
                    if raise_poison:
                        raise ValueError("Missing required fields")
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
                expires_at = _parse_expires_at(data.get("expires_at"))
                for attempt in range(5):
                    short_code = generate_short_code()
                    try:
                        # One savepoint per attempt: a failed INSERT must not
                        # poison the batch transaction (Postgres aborts it),
                        # and the request_id lookup below needs a usable
                        # transaction (#113).
                        with db.savepoint():
                            create_kwargs = {
                                "user_id": user_id,
                                "short_code": short_code,
                                "original_url": original_url,
                                "title": title,
                                "is_active": True,
                                "request_id": request_id,
                            }
                            if expires_at is not None:
                                create_kwargs["expires_at"] = expires_at
                            url = Url.create(**create_kwargs)
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
                    except (DataError, ValueError, TypeError):
                        if raise_poison:
                            # Permanent per-message failure → durable quarantine upstream.
                            raise
                        break  # legacy path: record per-message error status
                    except Exception:
                        continue

                if url is None:
                    if raise_poison:
                        raise IntegrityError("URL creation failed after bounded retries")
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
                            "expires_at": _expires_at_iso(getattr(url, "expires_at", None)),
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
    except (IntegrityError, DataError, ValueError, TypeError):
        if raise_poison:
            raise
        logger.exception("Invalid url-create batch")
        return False, []
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
