import json
import logging
import os
import time

from confluent_kafka import Producer

logger = logging.getLogger("quadroPE.kafka")

_producer = None

# Delivery outcomes reported via producer callbacks (#139). Failures are
# logged with topic/partition context instead of vanishing silently.
_delivery_ok = 0
_delivery_failed = 0

# Throttle for the opportunistic poll that drives delivery callbacks (#246).
# _produce() already poll(0)s after each message, but librdkafka only fires
# callbacks for completions that are ready at poll time, so counters lagged
# until flush() at exit. _maybe_poll() drives prior completions on entry.
_last_poll = 0.0


def _poll_interval():
    try:
        return max(0.0, float(os.environ.get("KAFKA_POLL_INTERVAL_SEC", 1.0)))
    except (TypeError, ValueError):
        return 1.0


def _maybe_poll(producer=None):
    """Best-effort non-blocking poll, throttled to _poll_interval (#246).

    No background threads: lifecycle is explicit, so callbacks are driven
    opportunistically on the calling thread. Never raises; polling must not
    break the produce path. Never creates the producer as a side effect.
    """
    global _last_poll
    try:
        now = time.monotonic()
        if now - _last_poll < _poll_interval():
            return
        _last_poll = now
        target = producer if producer is not None else _producer
        if target is not None:
            target.poll(0)
    except Exception:
        logger.exception("Kafka poll failed")


def _on_delivery(err, msg):
    global _delivery_ok, _delivery_failed
    if err is not None:
        _delivery_failed += 1
        logger.error(f"Kafka delivery failed topic={msg.topic() if msg else '?'}: {err}")
    else:
        _delivery_ok += 1


def delivery_stats():
    """Return delivery-callback counters, driving callbacks first (#246).

    Polls non-blocking before reading so completions surface without
    requiring flush(). Best-effort: if the broker hasn't completed a
    delivery yet, the counter still lags until the next poll.
    """
    try:
        if _producer is not None:
            _producer.poll(0)
    except Exception:
        logger.exception("Kafka poll failed")
    return {"delivered": _delivery_ok, "failed": _delivery_failed}


class ProducerBackpressureError(Exception):
    """Raised when the Kafka producer queue stays full despite retrying."""


def _get_producer():
    global _producer
    if _producer is None:
        broker = os.environ.get("KAFKA_BROKER", "kafka:9092")
        _producer = Producer(
            {
                "bootstrap.servers": broker,
                "queue.buffering.max.messages": int(
                    os.environ.get("KAFKA_BUFFER_MAX_MESSAGES", 200000)
                ),
                "queue.buffering.max.kbytes": int(
                    os.environ.get("KAFKA_BUFFER_MAX_KBYTES", 102400)
                ),
                "linger.ms": 5,
                "batch.num.messages": 1000,
            }
        )
        logger.info(f"Kafka producer initialized: {broker}")
    return _producer


def get_producer():
    return _get_producer()


def _produce(topic, data, key=None):
    """Produce a message with bounded backpressure handling.

    Retries when the broker buffer is full (BufferError).  If the queue stays
    full past the timeout, raises :class:`ProducerBackpressureError` so callers
    surface the stall instead of silently dropping the message.

    :param key: Kafka partition key — same key lands on the same partition,
        preserving per-entity ordering (#161).
    """
    producer = _get_producer()
    _maybe_poll(producer)
    payload = json.dumps(data).encode("utf-8")
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key

    deadline = time.time() + float(os.environ.get("KAFKA_PRODUCE_TIMEOUT", 5.0))
    while True:
        try:
            producer.produce(topic, value=payload, key=key_bytes, callback=_on_delivery)
            producer.poll(0)
            return
        except BufferError:
            if time.time() >= deadline:
                logger.error(f"Kafka producer queue full for topic={topic}, message dropped")
                raise ProducerBackpressureError(f"Kafka producer buffer full for topic={topic}")
            producer.poll(0.2)
        except Exception:
            logger.exception(f"Failed to publish to Kafka topic={topic}")
            raise


def _sync_write(model, **kwargs):
    """Direct DB fallback used when KAFKA_SYNC_FALLBACK=1 (tests / local dev)."""
    from app.database import db

    db.connect(reuse_if_open=True)
    with db.atomic():
        return model.create(**kwargs)


def _coerce_expires_at(value):
    """Tolerant ``expires_at`` coercion for the sync-fallback path (#192).

    The route layer already ran :func:`parse_expires_at` validation, so this
    never aborts: unparseable input yields ``None``. Naive values are assumed
    UTC (PG ``TIMESTAMP`` is tz-naive). Kept local (not imported from
    ``app.utils.validation``) to mirror the standalone consumer, which cannot
    import ``app``.
    """
    from datetime import datetime, timezone

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
    from datetime import datetime, timezone

    if value is None or isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return None


def publish_log_event(data: dict):
    topic = os.environ.get("KAFKA_TOPIC_REQUEST_LOGS", "request-logs")
    if os.environ.get("KAFKA_SYNC_FALLBACK") == "1":
        from app.models.request_log import RequestLog

        _sync_write(
            RequestLog,
            user_agent=data.get("user_agent", ""),
            client_ip=data.get("client_ip", ""),
            method=data.get("method", ""),
            path=data.get("path", ""),
            status_code=data.get("status_code", 0),
            latency_ms=data.get("latency_ms", 0.0),
            short_code=data.get("short_code", ""),
        )
        return
    # Key by short code (or path) so one link's logs stay ordered (#161).
    _produce(topic, data, key=data.get("short_code") or data.get("path"))


def publish_event(data: dict):
    topic = os.environ.get("KAFKA_TOPIC_URL_EVENTS", "url-events")
    if os.environ.get("KAFKA_SYNC_FALLBACK") == "1":
        from app.models.event import Event

        details = data.get("details", {})
        if isinstance(details, dict):
            details = json.dumps(details)
        from app.database import db, models
        from app.routes.delivery import delivery
        from shared.delivery import schedule_milestones

        db.connect(reuse_if_open=True)
        with db.atomic():
            if data.get("event_type") == "click":
                models.Url.update(updated_at=models.Url.updated_at).where(
                    models.Url.id == data.get("url_id")
                ).execute()
            row = Event.create(
                url_id=data.get("url_id"),
                user_id=data.get("user_id"),
                event_type=data.get("event_type"),
                details=details,
            )
            if row.event_type == "click":
                schedule_milestones(models, delivery, row.url_id)
        return
    # Key by URL so one link's events stay ordered on one partition (#161).
    url_id = data.get("url_id")
    _produce(topic, data, key=str(url_id) if url_id is not None else None)


def publish_url_create(data: dict):
    topic = os.environ.get("KAFKA_TOPIC_URL_CREATES", "url-creates")
    if os.environ.get("KAFKA_SYNC_FALLBACK") == "1":
        return _create_url_sync(data)
    # Key by request_id so retries of the same creation stay ordered (#161).
    _produce(topic, data, key=data.get("request_id"))
    return None


def _create_url_sync(data):
    """Synchronous URL creation used when KAFKA_SYNC_FALLBACK=1."""
    import secrets
    import string

    from peewee import IntegrityError

    from app.cache import set_url, set_url_by_short_code
    from app.database import db
    from app.models.url import Url
    from playhouse.shortcuts import model_to_dict

    user_id = data.get("user_id")
    original_url = data.get("original_url")
    title = data.get("title")
    request_id = data.get("request_id")
    expires_at = _coerce_expires_at(data.get("expires_at"))

    db.connect(reuse_if_open=True)
    url = None
    deduplicated = False
    for _ in range(5):
        short_code = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(6))
        try:
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
            # A request_id clash means this idempotency key already stored a
            # row (client retry raced past the route-level check): return the
            # existing row instead of failing (#113). Anything else is a
            # short-code clash, so fall through to the next attempt.
            if request_id:
                try:
                    url = Url.get(Url.request_id == request_id)
                    deduplicated = True
                    break
                except Url.DoesNotExist:
                    pass
            continue
        except Exception:
            continue
    if url is None:
        raise RuntimeError("Failed to generate unique short code")

    result = model_to_dict(url, recurse=False)
    result["user_id"] = result.pop("user")
    # Serialize from the stored row (not the request): idempotent replays of
    # the same key must echo the original expiry (#113). PG TIMESTAMP strips
    # tz on refetch, so naive values are normalized back to UTC.
    result["expires_at"] = _expires_at_iso(getattr(url, "expires_at", None))
    set_url(url.id, result)
    set_url_by_short_code(url.short_code, result)

    if not deduplicated:
        embed_url_best_effort(url)

    if deduplicated:
        # The winning attempt already emitted the "created" event; only the
        # cache calls are mirrored so a second event is never recorded (#113).
        return result

    publish_event(
        {
            "url_id": url.id,
            "user_id": url.user_id,
            "event_type": "created",
            "details": {
                "short_code": url.short_code,
                "original_url": url.original_url,
            },
        }
    )
    return result


def embed_url_best_effort(url):
    """Best-effort embedding for one URL row (sync create, title re-embed).

    No API key (tests, local dev) degrades to a no-op. Any failure only skips
    the embedding row — the URL write already succeeded.
    """
    try:
        from app.database import db
        from app.models.url_embedding import UrlEmbedding
        from shared.openrouter import DEFAULT_EMBEDDING_MODEL, api_key
        from shared.semantic import embed_links_batch

        key = api_key()
        if not key:
            return
        model = os.environ.get("OPENROUTER_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        embed_links_batch(
            db, UrlEmbedding, [(url.id, url.title, url.original_url)], model=model, key=key
        )
    except Exception:
        logger.warning("Sync-fallback embed skipped", exc_info=True)


def flush_producer():
    global _producer
    if _producer is not None:
        try:
            _producer.flush(timeout=5)
        except Exception:
            logger.exception("Failed to flush Kafka producer")
