"""Bounded Kafka lag snapshot using the installed AdminClient API."""

import os
import threading
import time

from confluent_kafka import ConsumerGroupTopicPartitions
from confluent_kafka.admin import AdminClient, OffsetSpec

_lock = threading.Lock()
_cache = {}
TTL = 10
TIMEOUT = 3


def reset_cache():
    with _lock:
        _cache.clear()


def snapshot(brokers=None):
    brokers = brokers or os.getenv("KAFKA_BROKER", "localhost:9092")
    prefix = os.getenv("KAFKA_GROUP", "request-log-writer")
    key = (brokers, prefix)
    with _lock:
        now = time.monotonic()
        cached = _cache.get(key)
        if cached and now - cached[0] < TTL:
            return cached[1]
        value = _probe(brokers, prefix)
        _cache.clear()
        _cache[key] = (time.monotonic(), value)
        return value


def _probe(brokers, prefix):
    try:
        admin = AdminClient({"bootstrap.servers": brokers})
        deadline = time.monotonic() + TIMEOUT

        def remaining():
            return max(0.001, deadline - time.monotonic())

        # The installed API accepts ONE group per call and returns a dict of futures.
        pending = {}
        for kind in ("logs", "events", "creates"):
            group = f"{prefix}-{kind}"
            pending[group] = admin.list_consumer_group_offsets(
                [ConsumerGroupTopicPartitions(group)], request_timeout=remaining()
            )[group]
        committed = {
            group: future.result(timeout=remaining()).topic_partitions
            for group, future in pending.items()
        }
        partitions = {tp: OffsetSpec.latest() for offsets in committed.values() for tp in offsets}
        ends = admin.list_offsets(partitions, request_timeout=remaining()) if partitions else {}
        high = {tp: future.result(timeout=remaining()).offset for tp, future in ends.items()}
        groups = {}
        for group, offsets in committed.items():
            # Uninitialized/no committed offsets are unknown, not fake zero lag.
            if not offsets or any(tp.offset < 0 or tp.error or high[tp] < 0 for tp in offsets):
                groups[group] = None
            else:
                groups[group] = sum(max(0, high[tp] - tp.offset) for tp in offsets)
        return {"available": True, "groups": groups}
    except Exception:
        return {"available": False, "groups": {}, "error": "Kafka lag unavailable"}
