"""Regression coverage for the bounded log buffer (#167), without services."""

from collections import deque
import logging
from unittest.mock import MagicMock

import pytest

from app import log_store
from app.utils import logger


@pytest.fixture(autouse=True)
def clean_tables():
    """Override the integration DB fixture: this test uses only an isolated buffer."""
    yield


def test_log_buffer_overflow_evicts_oldest_records(monkeypatch):
    capacity = log_store.log_records.maxlen
    assert capacity == 200
    # Preserve the production buffer and any existing records for other tests.
    buffer = deque(maxlen=capacity)
    monkeypatch.setattr(logger, "log_records", buffer)
    handler = logger.ListHandler()
    handler.handleError = MagicMock()
    for index in range(capacity + 3):
        handler.handle(
            logging.LogRecord("capacity", logging.INFO, __file__, 1, "record-%s", (index,), None)
        )

    records = list(buffer)
    handler.handleError.assert_not_called()
    assert len(records) == capacity
    assert [record["message"] for record in records] == [
        f"record-{index}" for index in range(3, capacity + 3)
    ]
    assert all(record["level"] == "INFO" for record in records)
    assert all(record["logger"] == "capacity" for record in records)
