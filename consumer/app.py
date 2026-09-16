import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

import redis
from peewee import DataError, IntegrityError
from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
from models import Event, RequestLog, db, delivery_models, models
from shared.delivery import Poison, decode_message, persist_rows, quarantine, schedule_milestones

import config
from retention import purge_request_logs_older_than
from url_create_handler import handle_url_create_batch


class ConsumerJsonFormatter(logging.Formatter):
    """JSON formatter emitting the same base keys as the app's JsonFormatter.

    The consumer image packages the worker and shared schema, not the Flask
    ``app`` package, so this is a local copy of the same
    ``timestamp``/``level``/``message``/``logger`` shape rather than an
    import. The consumer never runs inside a Flask request context, so
    the request-scoped keys (``method``/``path``/``remote_addr``/
    ``request_id``) do not apply here.
    """

    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


def setup_consumer_logging():
    """Configure the ``consumer`` logger once; repeat calls are no-ops."""
    consumer_logger = logging.getLogger("consumer")
    if consumer_logger.handlers:
        return consumer_logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ConsumerJsonFormatter())
    consumer_logger.addHandler(handler)
    consumer_logger.setLevel(logging.INFO)
    consumer_logger.propagate = False
    return consumer_logger


logger = setup_consumer_logging()

running = True


def handle_signal(signum, frame):
    global running
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    running = False


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def _heartbeat_path():
    """Path of the liveness heartbeat file for this consumer type (#174)."""
    ctype = getattr(config, "CONSUMER_TYPE", "unknown") or "unknown"
    return os.environ.get("CONSUMER_HEARTBEAT_FILE", f"/tmp/consumer-{ctype}.heartbeat")


def _beat():
    """Refresh the heartbeat file; never raise (a heartbeat must not crash a consumer)."""
    try:
        with open(_heartbeat_path(), "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def create_consumer(group_id):
    return Consumer(
        {
            "bootstrap.servers": config.KAFKA_BROKER,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "max.poll.interval.ms": 300000,
            "session.timeout.ms": 30000,
        }
    )


def commit_buffer(consumer, buffered):
    """Commit offsets AFTER the last successfully-buffered message per partition.

    ``buffered`` holds ``(payload, message)`` tuples.  Only the offsets of
    messages actually drained are committed, so any message discarded during a
    stalled period is redelivered rather than silently skipped.
    """
    by_partition = {}
    for _, msg in buffered:
        key = (msg.topic(), msg.partition())
        offset = msg.offset()
        if key not in by_partition or offset > by_partition[key]:
            by_partition[key] = offset
    if not by_partition:
        return
    offsets = [
        TopicPartition(topic, partition, offset + 1)
        for (topic, partition), offset in by_partition.items()
    ]
    consumer.commit(offsets=offsets, asynchronous=False)


def drain_request_logs(buffer):
    if not buffer:
        return True

    rows = [payload for payload, _ in buffer]
    start = time.time()
    try:
        db.connect(reuse_if_open=True)
        persist_rows(buffer, db, models, delivery_models, RequestLog)
        elapsed = time.time() - start
        logger.info(f"[request-logs] Inserted {len(rows)} records in {elapsed:.2f}s")
        return True
    except Exception:
        logger.exception("[request-logs] Failed to insert batch")
        return False
    finally:
        if not db.is_closed():
            db.close()


def drain_url_events(buffer):
    if not buffer:
        return True

    rows = [payload for payload, _ in buffer]
    start = time.time()
    try:
        db.connect(reuse_if_open=True)
        persist_rows(
            buffer,
            db,
            models,
            delivery_models,
            Event,
            lambda row: (
                schedule_milestones(models, delivery_models, row.url_id)
                if row.event_type == "click"
                else None
            ),
        )
        elapsed = time.time() - start
        logger.info(f"[url-events] Inserted {len(rows)} records in {elapsed:.2f}s")
        return True
    except Exception:
        logger.exception("[url-events] Failed to insert batch")
        return False
    finally:
        if not db.is_closed():
            db.close()


def drain_url_creates(buffer, redis_client):
    events = []
    try:
        db.connect(reuse_if_open=True)
        for payload, msg in buffer:
            if isinstance(payload, Poison):
                with db.atomic():
                    quarantine(msg, payload.error, delivery_models)
                continue
            try:
                ok, created = handle_url_create_batch(
                    [payload],
                    db,
                    redis_client,
                    raise_poison=True,
                )
                if not ok:
                    return False, []
                events.extend(created)
            except (IntegrityError, DataError, ValueError, TypeError) as exc:
                with db.atomic():
                    quarantine(msg, type(exc).__name__, delivery_models)
        return True, events
    except Exception:
        logger.exception("[url-creates] Durable drain failed")
        return False, []
    finally:
        if not db.is_closed():
            db.close()


def emit_created_events(producer, events):
    for event in events:
        try:
            key = event.get("url_id")
            producer.produce(
                config.KAFKA_TOPIC_URL_EVENTS,
                value=json.dumps(event).encode("utf-8"),
                key=str(key).encode("utf-8") if key is not None else None,
            )
        except Exception:
            logger.exception("[url-creates] Failed to publish created event")
    producer.poll(0)


def run_request_log_consumer():
    consumer = create_consumer(f"{config.KAFKA_GROUP}-logs")
    consumer.subscribe([config.KAFKA_TOPIC_REQUEST_LOGS])
    logger.info(
        f"[request-logs] Subscribed to {config.KAFKA_TOPIC_REQUEST_LOGS}, "
        f"drain={config.DRAIN_INTERVAL_LOGS}s, batch={config.BATCH_SIZE_LOGS}"
    )

    buffer = []
    last_drain = time.time()
    last_purge = time.time()
    stalled = False

    def drain():
        nonlocal stalled
        if drain_request_logs(buffer):
            commit_buffer(consumer, buffer)
            buffer.clear()
            stalled = False
        else:
            stalled = True
        return True

    while running:
        _beat()
        if stalled:
            # Don't poll while the buffer can't drain — polling now
            # would discard the message (#115). Back off and retry.
            time.sleep(1.0)
            drain()
            last_drain = time.time()
            continue
        msg = consumer.poll(timeout=1.0)

        if msg is None:
            now = time.time()
            if buffer and (now - last_drain >= config.DRAIN_INTERVAL_LOGS):
                drain()
                last_drain = now
            # Retention purge (#167): bounded, off the hot path (idle only).
            if now - last_purge >= getattr(config, "RETENTION_INTERVAL_LOGS", 3600):
                try:
                    purged = purge_request_logs_older_than(
                        db, RequestLog, getattr(config, "RETENTION_SECONDS_LOGS", 30 * 24 * 3600)
                    )
                    if purged:
                        logger.info(f"[request-logs] Purged {purged} rows past retention")
                except Exception:
                    logger.exception("[request-logs] Retention purge failed (continuing)")
                last_purge = now
            continue

        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            logger.error(f"[request-logs] Kafka error: {msg.error()}")
            continue

        try:
            data = decode_message(msg)
            payload = {
                "user_agent": data.get("user_agent", ""),
                "client_ip": data.get("client_ip", ""),
                "method": data.get("method", ""),
                "path": data.get("path", ""),
                "status_code": data.get("status_code", 0),
                "latency_ms": data.get("latency_ms", 0.0),
                "short_code": data.get("short_code", ""),
                "created_at": data.get("created_at") or datetime.now(timezone.utc),
            }
            buffer.append((payload, msg))
        except (ValueError, UnicodeDecodeError, AttributeError, TypeError) as e:
            buffer.append((Poison(type(e).__name__), msg))

        now = time.time()
        if len(buffer) >= config.BATCH_SIZE_LOGS:
            drain()
            last_drain = now
        elif now - last_drain >= config.DRAIN_INTERVAL_LOGS:
            drain()
            last_drain = now

    if buffer:
        if drain_request_logs(buffer):
            commit_buffer(consumer, buffer)
    consumer.close()


def run_url_event_consumer():
    consumer = create_consumer(f"{config.KAFKA_GROUP}-events")
    consumer.subscribe([config.KAFKA_TOPIC_URL_EVENTS])
    logger.info(
        f"[url-events] Subscribed to {config.KAFKA_TOPIC_URL_EVENTS}, "
        f"drain={config.DRAIN_INTERVAL_EVENTS}s, batch={config.BATCH_SIZE_EVENTS}"
    )

    buffer = []
    last_drain = time.time()
    stalled = False

    def drain():
        nonlocal stalled
        if drain_url_events(buffer):
            commit_buffer(consumer, buffer)
            buffer.clear()
            stalled = False
        else:
            stalled = True
        return True

    while running:
        _beat()
        if stalled:
            # Don't poll while the buffer can't drain — polling now
            # would discard the message (#115). Back off and retry.
            time.sleep(1.0)
            drain()
            last_drain = time.time()
            continue
        msg = consumer.poll(timeout=1.0)

        if msg is None:
            now = time.time()
            if buffer and (now - last_drain >= config.DRAIN_INTERVAL_EVENTS):
                drain()
                last_drain = now
            continue

        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            logger.error(f"[url-events] Kafka error: {msg.error()}")
            continue

        try:
            data = decode_message(msg)
            details = data.get("details", {})
            if isinstance(details, dict):
                details = json.dumps(details)
            payload = {
                "url_id": data.get("url_id", 0),
                "user_id": data.get("user_id", 0),
                "event_type": data.get("event_type", ""),
                "details": details,
                "timestamp": data.get("created_at") or data.get("timestamp") or None,
            }
            buffer.append((payload, msg))
        except (ValueError, UnicodeDecodeError, AttributeError, TypeError) as e:
            buffer.append((Poison(type(e).__name__), msg))

        now = time.time()
        if len(buffer) >= config.BATCH_SIZE_EVENTS:
            drain()
            last_drain = now
        elif now - last_drain >= config.DRAIN_INTERVAL_EVENTS:
            drain()
            last_drain = now

    if buffer:
        if drain_url_events(buffer):
            commit_buffer(consumer, buffer)
    consumer.close()


def run_url_create_consumer():
    consumer = create_consumer(f"{config.KAFKA_GROUP}-creates")
    consumer.subscribe([config.KAFKA_TOPIC_URL_CREATES])
    redis_client = redis.from_url(config.REDIS_URL, socket_timeout=2)
    event_producer = Producer({"bootstrap.servers": config.KAFKA_BROKER})
    logger.info(
        f"[url-creates] Subscribed to {config.KAFKA_TOPIC_URL_CREATES}, "
        f"drain={config.DRAIN_INTERVAL_CREATES}s, batch={config.BATCH_SIZE_CREATES}"
    )

    buffer = []
    last_drain = time.time()
    stalled = False

    def drain():
        nonlocal stalled
        ok, events = drain_url_creates(buffer, redis_client)
        if ok:
            commit_buffer(consumer, buffer)
            buffer.clear()
            emit_created_events(event_producer, events)
            stalled = False
        else:
            stalled = True
        return True

    while running:
        _beat()
        if stalled:
            # Don't poll while the buffer can't drain — polling now
            # would discard the message (#115). Back off and retry.
            time.sleep(1.0)
            drain()
            last_drain = time.time()
            continue
        msg = consumer.poll(timeout=1.0)

        if msg is None:
            now = time.time()
            if buffer and (now - last_drain >= config.DRAIN_INTERVAL_CREATES):
                drain()
                last_drain = now
            continue

        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            logger.error(f"[url-creates] Kafka error: {msg.error()}")
            continue

        try:
            data = decode_message(msg)
            buffer.append((data, msg))
        except (ValueError, UnicodeDecodeError, AttributeError, TypeError) as e:
            buffer.append((Poison(type(e).__name__), msg))

        now = time.time()
        if len(buffer) >= config.BATCH_SIZE_CREATES:
            drain()
            last_drain = now
        elif now - last_drain >= config.DRAIN_INTERVAL_CREATES:
            drain()
            last_drain = now

    if buffer:
        ok, events = drain_url_creates(buffer, redis_client)
        if ok:
            commit_buffer(consumer, buffer)
            emit_created_events(event_producer, events)

    consumer.close()
    redis_client.close()
    event_producer.flush(timeout=5)


def main():
    consumer_type = config.CONSUMER_TYPE
    logger.info(
        f"Starting Kafka consumer for type={consumer_type}, "
        f"broker={config.KAFKA_BROKER}, group={config.KAFKA_GROUP}"
    )

    runners = {
        "logs": run_request_log_consumer,
        "events": run_url_event_consumer,
        "creates": run_url_create_consumer,
    }
    runner = runners.get(consumer_type)
    if runner is None:
        logger.error(
            f"Unknown CONSUMER_TYPE={consumer_type}. Must be one of: logs, events, creates"
        )
        sys.exit(1)

    runner()


if __name__ == "__main__":
    main()
