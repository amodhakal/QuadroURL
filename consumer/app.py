import json
import logging
import signal
import sys
import time

import redis
from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DateTimeField,
    FloatField,
    IntegerField,
    Model,
    TextField,
)
from playhouse.pool import PooledPostgresqlDatabase

import config
from url_create_handler import handle_url_create_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("consumer")

_MAX_CONNECTIONS = {
    "logs": config.DB_MAX_CONNECTIONS_LOGS,
    "events": config.DB_MAX_CONNECTIONS_EVENTS,
    "creates": config.DB_MAX_CONNECTIONS_CREATES,
}

db = PooledPostgresqlDatabase(
    config.DATABASE_NAME,
    host=config.DATABASE_HOST,
    port=config.DATABASE_PORT,
    user=config.DATABASE_USER,
    password=config.DATABASE_PASSWORD,
    max_connections=_MAX_CONNECTIONS.get(config.CONSUMER_TYPE, 10),
    stale_timeout=300,
    connect_timeout=5,
)


class RequestLog(Model):
    id = IntegerField(primary_key=True)
    url_id = IntegerField(null=True)
    user_agent = TextField(default="")
    client_ip = CharField(default="")
    method = CharField()
    path = CharField()
    status_code = IntegerField()
    latency_ms = FloatField()
    short_code = CharField(default="")
    created_at = DateTimeField()

    class Meta:
        database = db
        table_name = "requestlog"


class Event(Model):
    id = AutoField()
    url_id = IntegerField()
    user_id = IntegerField()
    event_type = CharField()
    timestamp = DateTimeField()
    details = TextField()

    class Meta:
        database = db
        table_name = "event"


running = True


def handle_signal(signum, frame):
    global running
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    running = False


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def create_consumer(group_id):
    return Consumer({
        "bootstrap.servers": config.KAFKA_BROKER,
        "group.id": group_id,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "max.poll.interval.ms": 300000,
        "session.timeout.ms": 30000,
    })


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
        with db.atomic():
            RequestLog.insert_many(rows).execute()
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
        with db.atomic():
            Event.insert_many(rows).execute()
        elapsed = time.time() - start
        logger.info(f"[url-events] Inserted {len(rows)} records in {elapsed:.2f}s")
        return True
    except Exception:
        logger.exception("[url-events] Failed to insert batch")
        return False
    finally:
        if not db.is_closed():
            db.close()


def emit_created_events(producer, events):
    for event in events:
        try:
            producer.produce(
                config.KAFKA_TOPIC_URL_EVENTS,
                value=json.dumps(event).encode("utf-8"),
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
        msg = consumer.poll(timeout=1.0)

        if msg is None:
            now = time.time()
            if buffer and (now - last_drain >= config.DRAIN_INTERVAL_LOGS):
                drain()
                last_drain = now
            continue

        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            logger.error(f"[request-logs] Kafka error: {msg.error()}")
            continue

        if stalled:
            continue

        try:
            data = json.loads(msg.value().decode("utf-8"))
            payload = {
                "user_agent": data.get("user_agent", ""),
                "client_ip": data.get("client_ip", ""),
                "method": data.get("method", ""),
                "path": data.get("path", ""),
                "status_code": data.get("status_code", 0),
                "latency_ms": data.get("latency_ms", 0.0),
                "short_code": data.get("short_code", ""),
                "created_at": data.get("created_at", ""),
            }
            buffer.append((payload, msg))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"[request-logs] Failed to decode message: {e}")
            continue

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

        if stalled:
            continue

        try:
            data = json.loads(msg.value().decode("utf-8"))
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
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"[url-events] Failed to decode message: {e}")
            continue

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
        messages = [payload for payload, _ in buffer]
        ok, events = handle_url_create_batch(messages, db, redis_client)
        if ok:
            commit_buffer(consumer, buffer)
            buffer.clear()
            emit_created_events(event_producer, events)
            stalled = False
        else:
            stalled = True
        return True

    while running:
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

        if stalled:
            continue

        try:
            data = json.loads(msg.value().decode("utf-8"))
            buffer.append((data, msg))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"[url-creates] Failed to decode message: {e}")
            continue

        now = time.time()
        if len(buffer) >= config.BATCH_SIZE_CREATES:
            drain()
            last_drain = now
        elif now - last_drain >= config.DRAIN_INTERVAL_CREATES:
            drain()
            last_drain = now

    if buffer:
        messages = [payload for payload, _ in buffer]
        ok, events = handle_url_create_batch(messages, db, redis_client)
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
        logger.error(f"Unknown CONSUMER_TYPE={consumer_type}. Must be one of: logs, events, creates")
        sys.exit(1)

    runner()


if __name__ == "__main__":
    main()