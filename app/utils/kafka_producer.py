import json
import logging
import os
import time

from confluent_kafka import Producer

logger = logging.getLogger("quadroPE.kafka")

_producer = None


class ProducerBackpressureError(Exception):
    """Raised when the Kafka producer queue stays full despite retrying."""


def _get_producer():
    global _producer
    if _producer is None:
        broker = os.environ.get("KAFKA_BROKER", "kafka:9092")
        _producer = Producer({
            "bootstrap.servers": broker,
            "queue.buffering.max.messages": int(
                os.environ.get("KAFKA_BUFFER_MAX_MESSAGES", 200000)
            ),
            "queue.buffering.max.kbytes": int(
                os.environ.get("KAFKA_BUFFER_MAX_KBYTES", 102400)
            ),
            "linger.ms": 5,
            "batch.num.messages": 1000,
        })
        logger.info(f"Kafka producer initialized: {broker}")
    return _producer


def get_producer():
    return _get_producer()


def _produce(topic, data):
    """Produce a message with bounded backpressure handling.

    Retries when the broker buffer is full (BufferError).  If the queue stays
    full past the timeout, raises :class:`ProducerBackpressureError` so callers
    surface the stall instead of silently dropping the message.
    """
    producer = _get_producer()
    payload = json.dumps(data).encode("utf-8")

    deadline = time.time() + float(os.environ.get("KAFKA_PRODUCE_TIMEOUT", 5.0))
    while True:
        try:
            producer.produce(topic, value=payload)
            producer.poll(0)
            return
        except BufferError:
            if time.time() >= deadline:
                logger.error(f"Kafka producer queue full for topic={topic}, message dropped")
                raise ProducerBackpressureError(
                    f"Kafka producer buffer full for topic={topic}"
                )
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
    _produce(topic, data)


def publish_event(data: dict):
    topic = os.environ.get("KAFKA_TOPIC_URL_EVENTS", "url-events")
    if os.environ.get("KAFKA_SYNC_FALLBACK") == "1":
        from app.models.event import Event
        details = data.get("details", {})
        if isinstance(details, dict):
            details = json.dumps(details)
        _sync_write(
            Event,
            url_id=data.get("url_id"),
            user_id=data.get("user_id"),
            event_type=data.get("event_type"),
            details=details,
        )
        return
    _produce(topic, data)


def publish_url_create(data: dict):
    topic = os.environ.get("KAFKA_TOPIC_URL_CREATES", "url-creates")
    if os.environ.get("KAFKA_SYNC_FALLBACK") == "1":
        return _create_url_sync(data)
    _produce(topic, data)
    return None


def _create_url_sync(data):
    """Synchronous URL creation used when KAFKA_SYNC_FALLBACK=1."""
    import random
    import string

    from app.cache import set_url, set_url_by_short_code
    from app.database import db
    from app.models.url import Url
    from playhouse.shortcuts import model_to_dict

    user_id = data.get("user_id")
    original_url = data.get("original_url")
    title = data.get("title")

    db.connect(reuse_if_open=True)
    url = None
    for _ in range(5):
        short_code = "".join(
            random.choices(string.ascii_letters + string.digits, k=6)
        )
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
        raise RuntimeError("Failed to generate unique short code")

    result = model_to_dict(url, recurse=False)
    result["user_id"] = result.pop("user")
    set_url(url.id, result)
    set_url_by_short_code(short_code, result)

    publish_event({
        "url_id": url.id,
        "user_id": url.user_id,
        "event_type": "created",
        "details": {
            "short_code": url.short_code,
            "original_url": url.original_url,
        },
    })
    return result


def flush_producer():
    global _producer
    if _producer is not None:
        try:
            _producer.flush(timeout=5)
        except Exception:
            logger.exception("Failed to flush Kafka producer")