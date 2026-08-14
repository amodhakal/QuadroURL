import json
import logging
import signal
import sys
import time

import redis
from confluent_kafka import Consumer, KafkaError
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
from url_create_handler import handle_url_create

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("consumer")

db = PooledPostgresqlDatabase(
    config.DATABASE_NAME,
    host=config.DATABASE_HOST,
    port=config.DATABASE_PORT,
    user=config.DATABASE_USER,
    password=config.DATABASE_PASSWORD,
    max_connections=10,
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


def drain_request_logs(buffer):
    if not buffer:
        return

    start = time.time()
    try:
        db.connect(reuse_if_open=True)
        with db.atomic():
            RequestLog.insert_many(buffer).execute()
        elapsed = time.time() - start
        logger.info(f"[request-logs] Inserted {len(buffer)} records in {elapsed:.2f}s")
        buffer.clear()
    except Exception:
        logger.exception("[request-logs] Failed to insert batch")
        buffer.clear()
    finally:
        if not db.is_closed():
            db.close()


def drain_url_events(buffer):
    if not buffer:
        return

    start = time.time()
    try:
        db.connect(reuse_if_open=True)
        with db.atomic():
            Event.insert_many(buffer).execute()
        elapsed = time.time() - start
        logger.info(f"[url-events] Inserted {len(buffer)} records in {elapsed:.2f}s")
        buffer.clear()
    except Exception:
        logger.exception("[url-events] Failed to insert batch")
        buffer.clear()
    finally:
        if not db.is_closed():
            db.close()


def run_request_log_consumer():
    consumer = create_consumer(f"{config.KAFKA_GROUP}-logs")
    consumer.subscribe([config.KAFKA_TOPIC_REQUEST_LOGS])
    logger.info(
        f"[request-logs] Subscribed to {config.KAFKA_TOPIC_REQUEST_LOGS}, "
        f"drain={config.DRAIN_INTERVAL_LOGS}s, batch={config.BATCH_SIZE_LOGS}"
    )

    buffer = []
    last_drain = time.time()

    while running:
        msg = consumer.poll(timeout=1.0)

        if msg is None:
            now = time.time()
            if buffer and (now - last_drain >= config.DRAIN_INTERVAL_LOGS):
                drain_request_logs(buffer)
                last_drain = now
            continue

        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            logger.error(f"[request-logs] Kafka error: {msg.error()}")
            continue

        try:
            data = json.loads(msg.value().decode("utf-8"))
            buffer.append({
                "user_agent": data.get("user_agent", ""),
                "client_ip": data.get("client_ip", ""),
                "method": data.get("method", ""),
                "path": data.get("path", ""),
                "status_code": data.get("status_code", 0),
                "latency_ms": data.get("latency_ms", 0.0),
                "short_code": data.get("short_code", ""),
                "created_at": data.get("created_at", ""),
            })
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"[request-logs] Failed to decode message: {e}")
            continue

        now = time.time()
        if len(buffer) >= config.BATCH_SIZE_LOGS:
            drain_request_logs(buffer)
            last_drain = now
        elif now - last_drain >= config.DRAIN_INTERVAL_LOGS:
            drain_request_logs(buffer)
            last_drain = now

    if buffer:
        drain_request_logs(buffer)
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

    while running:
        msg = consumer.poll(timeout=1.0)

        if msg is None:
            now = time.time()
            if buffer and (now - last_drain >= config.DRAIN_INTERVAL_EVENTS):
                drain_url_events(buffer)
                last_drain = now
            continue

        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            logger.error(f"[url-events] Kafka error: {msg.error()}")
            continue

        try:
            data = json.loads(msg.value().decode("utf-8"))
            details = data.get("details", {})
            if isinstance(details, dict):
                details = json.dumps(details)
            buffer.append({
                "url_id": data.get("url_id", 0),
                "user_id": data.get("user_id", 0),
                "event_type": data.get("event_type", ""),
                "details": details,
                "created_at": data.get("created_at", ""),
            })
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"[url-events] Failed to decode message: {e}")
            continue

        now = time.time()
        if len(buffer) >= config.BATCH_SIZE_EVENTS:
            drain_url_events(buffer)
            last_drain = now
        elif now - last_drain >= config.DRAIN_INTERVAL_EVENTS:
            drain_url_events(buffer)
            last_drain = now

    if buffer:
        drain_url_events(buffer)
    consumer.close()


def run_url_create_consumer():
    consumer = create_consumer(f"{config.KAFKA_GROUP}-creates")
    consumer.subscribe([config.KAFKA_TOPIC_URL_CREATES])
    redis_client = redis.from_url(config.REDIS_URL, socket_timeout=2)
    logger.info(
        f"[url-creates] Subscribed to {config.KAFKA_TOPIC_URL_CREATES}"
    )

    while running:
        msg = consumer.poll(timeout=1.0)

        if msg is None:
            continue

        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            logger.error(f"[url-creates] Kafka error: {msg.error()}")
            continue

        try:
            data = json.loads(msg.value().decode("utf-8"))
            handle_url_create(data, db, redis_client)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"[url-creates] Failed to decode message: {e}")
            continue

        consumer.commit(asynchronous=False)

    consumer.close()
    redis_client.close()


def main():
    logger.info("Starting multi-topic Kafka consumer")
    logger.info(
        f"Broker: {config.KAFKA_BROKER}, Group: {config.KAFKA_GROUP}"
    )

    import threading

    threads = [
        threading.Thread(target=run_request_log_consumer, name="request-log-consumer", daemon=True),
        threading.Thread(target=run_url_event_consumer, name="url-event-consumer", daemon=True),
        threading.Thread(target=run_url_create_consumer, name="url-create-consumer", daemon=True),
    ]

    for t in threads:
        t.start()

    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        global running
        running = False
        logger.info("Shutting down consumers...")
        for t in threads:
            t.join(timeout=10)
        logger.info("All consumers shut down")


if __name__ == "__main__":
    main()
