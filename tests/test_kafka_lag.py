"""Mocks match confluent-kafka dict-of-futures contracts."""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from confluent_kafka import TopicPartition

from app.utils import kafka_lag as lag


@pytest.fixture(autouse=True)
def clean_tables():
    lag.reset_cache()
    yield
    lag.reset_cache()


def future(value):
    f = Future()
    f.set_result(value)
    return f


def test_snapshot_uses_committed_and_end_offsets(monkeypatch):
    admin = Mock()

    def committed(requests, **kwargs):
        assert len(requests) == 1
        group = requests[0].group_id
        return {group: future(SimpleNamespace(topic_partitions=[TopicPartition("events", 0, 40)]))}

    admin.list_consumer_group_offsets.side_effect = committed
    admin.list_offsets.return_value = {
        TopicPartition("events", 0): future(SimpleNamespace(offset=45))
    }
    monkeypatch.setattr(lag, "AdminClient", Mock(return_value=admin))
    result = lag.snapshot("test:9092")
    assert result == {
        "available": True,
        "groups": {
            "request-log-writer-logs": 5,
            "request-log-writer-events": 5,
            "request-log-writer-creates": 5,
        },
    }
    assert lag.snapshot("test:9092") == result
    assert admin.list_consumer_group_offsets.call_count == 3


def test_failure_not_reported_as_zero(monkeypatch):
    monkeypatch.setattr(lag, "AdminClient", Mock(side_effect=RuntimeError("secret broker")))
    assert lag.snapshot() == {"available": False, "groups": {}, "error": "Kafka lag unavailable"}
