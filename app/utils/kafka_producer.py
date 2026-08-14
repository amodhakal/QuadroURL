import json
import logging
import os

from confluent_kafka import Producer

logger = logging.getLogger("quadroPE.kafka")

_producer = None


def _get_producer():
    global _producer
    if _producer is None:
        broker = os.environ.get("KAFKA_BROKER", "kafka:9092")
        _producer = Producer({
            "bootstrap.servers": broker,
            "queue.buffering.max.messages": 10000,
            "queue.buffering.max.kbytes": 10240,
            "linger.ms": 5,
            "batch.num.messages": 1000,
        })
        logger.info(f"Kafka producer initialized: {broker}")
    return _producer


def publish_log_event(data: dict):
    try:
        producer = _get_producer()
        topic = os.environ.get("KAFKA_TOPIC", "request-logs")
        producer.produce(topic, value=json.dumps(data).encode("utf-8"))
        producer.poll(0)
    except Exception:
        logger.exception("Failed to publish log event to Kafka")


def flush_producer():
    global _producer
    if _producer is not None:
        try:
            _producer.flush(timeout=5)
        except Exception:
            logger.exception("Failed to flush Kafka producer")
