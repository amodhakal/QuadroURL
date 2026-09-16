"""Regression coverage for the bounded log buffer (#167), without services."""

from collections import deque

import pytest

from app import log_store


@pytest.fixture(autouse=True)
def clean_tables():
    """Override the integration DB fixture: this test uses only an isolated buffer."""
    yield


def test_log_buffer_overflow_evicts_oldest_records(monkeypatch):
    capacity = log_store.log_records.maxlen
    assert capacity == 200
    # Preserve the production buffer and any existing records for other tests.
    monkeypatch.setattr(log_store, "log_records", deque(maxlen=capacity))
    for index in range(capacity + 3):
        with log_store.log_records_lock:
            log_store.log_records.append({"message": f"record-{index}"})

    with log_store.log_records_lock:
        records = list(log_store.log_records)

    assert len(records) == capacity
    assert records == [{"message": f"record-{index}"} for index in range(3, capacity + 3)]
