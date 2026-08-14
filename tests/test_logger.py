"""Tests for app/utils/logger.py — JSONFormatter and setup_logger."""

import json
import logging

import pytest

from app.utils.logger import JSONFormatter, setup_logger


@pytest.fixture(autouse=True)
def _cleanup_loggers():
    yield
    for name, logger in logging.Logger.manager.loggerDict.items():
        if isinstance(logger, logging.Logger):
            logger.handlers.clear()


def test_json_formatter_format():
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    fmt = JSONFormatter()
    result = json.loads(fmt.format(record))
    assert "timestamp" in result
    assert result["level"] == "INFO"
    assert result["message"] == "hello world"
    assert result["module"] == record.module


def test_setup_logger_returns_configured_logger():
    logger = setup_logger("testlogger1")
    assert logger.name == "testlogger1"
    assert logger.level == logging.INFO
    assert logger.propagate is False
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)
    assert isinstance(logger.handlers[0].formatter, JSONFormatter)


def test_setup_logger_is_idempotent():
    logger1 = setup_logger("testlogger2")
    assert len(logger1.handlers) == 1
    logger2 = setup_logger("testlogger2")
    assert logger2 is logger1
    assert len(logger2.handlers) == 1
