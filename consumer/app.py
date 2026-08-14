import json
import logging
import signal
import sys
import time

from confluent_kafka import Consumer, KafkaError, TopicPartition
from peewee import (
    CharField,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    TextField,
)
from playhouse.pool import PooledPostgresqlDatabase

import config

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


running = True


def handle_signal(signum, frame):
    global running
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    running = False


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def create_consumer():
    return Consumer({
        "bootstrap.servers": config.KAFKA_BROKER,
        "group.id": config.KAFKA_GROUP,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "max.poll.interval.ms": 300000,
        "session.timeout.ms": 30000,
    })


def drain_and_insert(buffer):
    if not buffer:
        return

    start = time.time()
    try:
        db.connect(reuse_if_open=True)

        with db.atomic():
            RequestLog.insert_many(buffer).execute()

        elapsed = time.time() - start
        logger.info(
            f"Inserted {len(buffer)} records in {elapsed:.2f}s"
        )
        buffer.clear()
    except Exception:
        logger.exception("Failed to insert batch into database")
        buffer.clear()
    finally:
        if not db.is_closed():
            db.close()


def main():
    logger.info("Starting request log consumer")
    logger.info(
        f"Broker: {config.KAFKA_BROKER}, Topic: {config.KAFKA_TOPIC}, "
        f"Group: {config.KAFKA_GROUP}, Drain: {config.DRAIN_INTERVAL}s, "
        f"Batch: {config.BATCH_SIZE}"
    )

    consumer = create_consumer()
    consumer.subscribe([config.KAFKA_TOPIC])

    buffer = []
    last_drain = time.time()

    try:
        while running:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                now = time.time()
                if buffer and (now - last_drain >= config.DRAIN_INTERVAL):
                    drain_and_insert(buffer)
                    last_drain = now
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error(f"Kafka error: {msg.error()}")
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
                logger.warning(f"Failed to decode message: {e}")
                continue

            now = time.time()
            if len(buffer) >= config.BATCH_SIZE:
                drain_and_insert(buffer)
                last_drain = now
            elif now - last_drain >= config.DRAIN_INTERVAL:
                drain_and_insert(buffer)
                last_drain = now

    finally:
        if buffer:
            drain_and_insert(buffer)
        consumer.close()
        logger.info("Consumer shut down")


if __name__ == "__main__":
    main()
