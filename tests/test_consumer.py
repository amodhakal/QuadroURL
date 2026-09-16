"""Hermetic unit tests for the standalone consumer package (#165, #164).

Covers ``consumer/app.py`` (commit helpers, drains, event emission,
JSON formatter, signal/logging setup) and ``consumer/url_create_handler.py``
(short codes, validation, create batch) with ``MagicMock`` stand-ins for
Kafka/Redis/DB handles: no network, no threads, no sleeps.

Isolation (#164): the consumer package uses top-level imports
(``import config``, ``from url_create_handler import ...``) and its entry
point is named ``app``, which collides with the Flask ``app`` package that
``tests/conftest.py`` already places in ``sys.modules``. The
``consumer_modules`` fixture therefore loads the consumer sources under
aliased module names via ``importlib.util.spec_from_file_location`` while
``monkeypatch.syspath_prepend`` confines the ``sys.path`` change and the
fixture restores ``sys.modules`` afterwards, so nothing leaks into
neighbouring test modules.
"""

import importlib.util
import json
import logging
import re
import signal
import string
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from confluent_kafka import TopicPartition

CONSUMER_DIR = Path(__file__).resolve().parent.parent / "consumer"
_CONSUMER_MODULE_NAMES = ("config", "url_create_handler", "consumer_app")
_MISSING = object()

CODE_RE = re.compile(r"[A-Za-z0-9]{6}\Z")


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def consumer_modules(monkeypatch):
    """Load consumer sources under aliased names; undo path/module changes."""
    monkeypatch.syspath_prepend(str(CONSUMER_DIR))
    saved = {name: sys.modules.get(name, _MISSING) for name in _CONSUMER_MODULE_NAMES}
    try:
        config_mod = _load_module("config", CONSUMER_DIR / "config.py")
        handler_mod = _load_module("url_create_handler", CONSUMER_DIR / "url_create_handler.py")
        app_mod = _load_module("consumer_app", CONSUMER_DIR / "app.py")
    except Exception:
        for name, mod in saved.items():
            if mod is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        raise
    try:
        yield SimpleNamespace(app=app_mod, handler=handler_mod, config=config_mod)
    finally:
        for name, mod in saved.items():
            if mod is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def _kafka_msg(topic="request-logs", partition=0, offset=0):
    msg = MagicMock(name="kafka_msg")
    msg.topic.return_value = topic
    msg.partition.return_value = partition
    msg.offset.return_value = offset
    return msg


def _fake_db(closed=False):
    db = MagicMock(name="fake_db")
    db.is_closed.return_value = closed
    return db


def _fake_redis():
    client = MagicMock(name="redis_client")
    return client, client.pipeline.return_value


def _valid_create(request_id="r1"):
    return {
        "request_id": request_id,
        "user_id": 7,
        "original_url": "https://example.com",
        "title": "Example",
    }


# ---------------------------------------------------------------------------
# commit_one (#114 poison-skip semantics)
# ---------------------------------------------------------------------------


def test_commit_one_commits_next_offset(consumer_modules):
    consumer = MagicMock(name="consumer")
    msg = _kafka_msg(topic="request-logs", partition=2, offset=41)
    consumer_modules.app.commit_one(consumer, msg)
    consumer.commit.assert_called_once()
    _, kwargs = consumer.commit.call_args
    assert kwargs["asynchronous"] is False
    (tp,) = kwargs["offsets"]
    assert isinstance(tp, TopicPartition)
    assert (tp.topic, tp.partition, tp.offset) == ("request-logs", 2, 42)


def test_commit_one_swallows_commit_errors(consumer_modules):
    """Poison messages must be skipped without raising (#114)."""
    consumer = MagicMock(name="consumer")
    consumer.commit.side_effect = RuntimeError("broker down")
    consumer_modules.app.commit_one(consumer, _kafka_msg(offset=9))
    consumer.commit.assert_called_once()


# ---------------------------------------------------------------------------
# commit_buffer
# ---------------------------------------------------------------------------


def test_commit_buffer_empty_commits_nothing(consumer_modules):
    consumer = MagicMock(name="consumer")
    consumer_modules.app.commit_buffer(consumer, [])
    consumer.commit.assert_not_called()


def test_commit_buffer_single_partition_commits_max_plus_one(consumer_modules):
    consumer = MagicMock(name="consumer")
    buffered = [({}, _kafka_msg(topic="t", partition=0, offset=o)) for o in (5, 7, 6)]
    consumer_modules.app.commit_buffer(consumer, buffered)
    consumer.commit.assert_called_once()
    _, kwargs = consumer.commit.call_args
    assert kwargs["asynchronous"] is False
    (tp,) = kwargs["offsets"]
    assert (tp.topic, tp.partition, tp.offset) == ("t", 0, 8)


def test_commit_buffer_multi_partition_commits_each_max(consumer_modules):
    consumer = MagicMock(name="consumer")
    buffered = [
        ({}, _kafka_msg(topic="t", partition=0, offset=3)),
        ({}, _kafka_msg(topic="t", partition=0, offset=4)),
        ({}, _kafka_msg(topic="t", partition=1, offset=9)),
    ]
    consumer_modules.app.commit_buffer(consumer, buffered)
    consumer.commit.assert_called_once()
    _, kwargs = consumer.commit.call_args
    got = {(tp.topic, tp.partition, tp.offset) for tp in kwargs["offsets"]}
    assert got == {("t", 0, 5), ("t", 1, 10)}


# ---------------------------------------------------------------------------
# generate_short_code
# ---------------------------------------------------------------------------


def test_generate_short_code_default_length_and_charset(consumer_modules):
    code = consumer_modules.handler.generate_short_code()
    assert len(code) == 6
    assert CODE_RE.match(code) is not None
    assert set(code) <= set(string.ascii_letters + string.digits)


def test_generate_short_code_custom_length(consumer_modules):
    assert len(consumer_modules.handler.generate_short_code(10)) == 10


def test_generate_short_code_unique(consumer_modules):
    codes = {consumer_modules.handler.generate_short_code() for _ in range(256)}
    assert len(codes) == 256


# ---------------------------------------------------------------------------
# _validate
# ---------------------------------------------------------------------------


def test_validate_returns_all_fields(consumer_modules):
    data = {
        "request_id": "r1",
        "user_id": 7,
        "original_url": "https://example.com",
        "title": "Example",
    }
    assert consumer_modules.handler._validate(data) == ("r1", 7, "https://example.com", "Example")


def test_validate_missing_fields_yield_none(consumer_modules):
    assert consumer_modules.handler._validate({}) == (None, None, None, None)
    assert consumer_modules.handler._validate({"request_id": "r"}) == ("r", None, None, None)


# ---------------------------------------------------------------------------
# handle_url_create_batch
# ---------------------------------------------------------------------------


def test_handle_url_create_batch_empty_returns_ok(consumer_modules):
    db = _fake_db()
    redis_client, _ = _fake_redis()
    ok, events = consumer_modules.handler.handle_url_create_batch([], db, redis_client)
    assert (ok, events) == (True, [])
    db.connect.assert_not_called()
    redis_client.pipeline.assert_not_called()


def test_handle_url_create_batch_success(consumer_modules, monkeypatch):
    handler = consumer_modules.handler
    created = SimpleNamespace(
        id=42,
        user_id=7,
        short_code="Ab3xYz",
        original_url="https://example.com",
        title="Example",
    )
    create_calls = []

    def fake_create(**kwargs):
        create_calls.append(kwargs)
        return created

    monkeypatch.setattr(handler.Url, "create", fake_create)
    db = _fake_db()
    redis_client, pipe = _fake_redis()

    ok, events = handler.handle_url_create_batch([_valid_create()], db, redis_client)

    assert ok is True
    assert len(events) == 1
    assert events[0]["url_id"] == 42
    assert events[0]["user_id"] == 7
    assert events[0]["event_type"] == "created"
    assert create_calls and set(create_calls[0]) == {
        "user_id",
        "short_code",
        "original_url",
        "title",
        "is_active",
    }
    pipe.setex.assert_called_once()
    key, ttl, raw = pipe.setex.call_args.args
    assert key == "url-pending:r1"
    assert ttl == handler.PENDING_TTL
    payload = json.loads(raw)
    assert payload["status"] == "ready"
    assert payload["id"] == 42
    assert payload["short_code"] == "Ab3xYz"
    pipe.execute.assert_called_once()


def test_handle_url_create_batch_invalid_message_yields_error(consumer_modules, monkeypatch):
    handler = consumer_modules.handler
    create_mock = MagicMock(name="Url.create")
    monkeypatch.setattr(handler.Url, "create", create_mock)
    db = _fake_db()
    redis_client, pipe = _fake_redis()

    ok, events = handler.handle_url_create_batch(
        [{"request_id": "r-bad", "title": "Only a title"}], db, redis_client
    )

    assert ok is True
    assert events == []
    create_mock.assert_not_called()
    pipe.setex.assert_called_once()
    key, _, raw = pipe.setex.call_args.args
    assert key == "url-pending:r-bad"
    payload = json.loads(raw)
    assert payload == {"status": "error", "error": "Missing required fields"}


def test_handle_url_create_batch_missing_request_id_writes_no_key(consumer_modules):
    handler = consumer_modules.handler
    db = _fake_db()
    redis_client, pipe = _fake_redis()
    ok, events = handler.handle_url_create_batch([{"user_id": 1}], db, redis_client)
    assert (ok, events) == (True, [])
    redis_client.pipeline.assert_not_called()
    pipe.setex.assert_not_called()


def test_handle_url_create_batch_mixed_batch(consumer_modules, monkeypatch):
    handler = consumer_modules.handler
    created = SimpleNamespace(
        id=1, user_id=7, short_code="Zz9qWw", original_url="https://a.example", title="A"
    )
    monkeypatch.setattr(handler.Url, "create", MagicMock(return_value=created))
    db = _fake_db()
    redis_client, pipe = _fake_redis()

    ok, events = handler.handle_url_create_batch(
        [_valid_create("r-good"), {"request_id": "r-bad"}], db, redis_client
    )

    assert ok is True
    assert len(events) == 1
    assert pipe.setex.call_count == 2
    statuses = {json.loads(call.args[2])["status"] for call in pipe.setex.call_args_list}
    assert statuses == {"ready", "error"}


def test_handle_url_create_batch_short_code_exhaustion_yields_error(consumer_modules, monkeypatch):
    handler = consumer_modules.handler
    create_mock = MagicMock(side_effect=Exception("collision"))
    monkeypatch.setattr(handler.Url, "create", create_mock)
    db = _fake_db()
    redis_client, pipe = _fake_redis()

    ok, events = handler.handle_url_create_batch([_valid_create()], db, redis_client)

    assert ok is True
    assert events == []
    assert create_mock.call_count == 5
    _, _, raw = pipe.setex.call_args.args
    assert json.loads(raw) == {"status": "error", "error": "Failed to generate unique short code"}


def test_handle_url_create_batch_db_error_returns_not_ok(consumer_modules):
    handler = consumer_modules.handler
    db = _fake_db()
    db.atomic.side_effect = RuntimeError("db down")
    redis_client, _ = _fake_redis()
    ok, events = handler.handle_url_create_batch([_valid_create()], db, redis_client)
    assert (ok, events) == (False, [])


def test_handle_url_create_batch_redis_error_returns_not_ok(consumer_modules, monkeypatch):
    handler = consumer_modules.handler
    created = SimpleNamespace(
        id=3, user_id=7, short_code="Qq1wEe", original_url="https://b.example", title="B"
    )
    monkeypatch.setattr(handler.Url, "create", MagicMock(return_value=created))
    db = _fake_db()
    redis_client, pipe = _fake_redis()
    pipe.execute.side_effect = RuntimeError("redis down")
    ok, events = handler.handle_url_create_batch([_valid_create()], db, redis_client)
    assert (ok, events) == (False, [])


# ---------------------------------------------------------------------------
# emit_created_events (#161: keyed by url_id)
# ---------------------------------------------------------------------------


def test_emit_created_events_keys_by_url_id(consumer_modules):
    app_mod = consumer_modules.app
    producer = MagicMock(name="producer")
    events = [
        {"url_id": 42, "user_id": 7, "event_type": "created", "details": {"a": 1}},
        {"url_id": 7, "user_id": 7, "event_type": "created", "details": {"b": 2}},
    ]
    app_mod.emit_created_events(producer, events)
    assert producer.produce.call_count == 2
    for call, event in zip(producer.produce.call_args_list, events):
        args, kwargs = call
        assert args[0] == consumer_modules.config.KAFKA_TOPIC_URL_EVENTS
        assert kwargs["value"] == json.dumps(event).encode("utf-8")
        assert kwargs["key"] == str(event["url_id"]).encode("utf-8")
        assert isinstance(kwargs["key"], bytes)
    producer.poll.assert_called_once_with(0)


def test_emit_created_events_none_key(consumer_modules):
    producer = MagicMock(name="producer")
    event = {"user_id": 7, "event_type": "created"}
    consumer_modules.app.emit_created_events(producer, [event])
    _, kwargs = producer.produce.call_args
    assert kwargs["key"] is None
    producer.poll.assert_called_once_with(0)


def test_emit_created_events_produce_error_does_not_raise(consumer_modules):
    producer = MagicMock(name="producer")
    producer.produce.side_effect = [RuntimeError("broker down"), None]
    events = [{"url_id": 1}, {"url_id": 2}]
    consumer_modules.app.emit_created_events(producer, events)
    assert producer.produce.call_count == 2
    producer.poll.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# ConsumerJsonFormatter
# ---------------------------------------------------------------------------


def test_consumer_json_formatter_keys(consumer_modules):
    record = logging.LogRecord("consumer", logging.INFO, __file__, 10, "hello %s", ("world",), None)
    out = json.loads(consumer_modules.app.ConsumerJsonFormatter().format(record))
    assert set(out) == {"timestamp", "level", "logger", "message"}
    assert out["message"] == "hello world"
    assert out["level"] == "INFO"
    assert out["logger"] == "consumer"
    assert out["timestamp"]


def test_consumer_json_formatter_exception(consumer_modules):
    try:
        raise ValueError("kaput")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord("consumer", logging.ERROR, __file__, 10, "failed", (), exc_info)
    out = json.loads(consumer_modules.app.ConsumerJsonFormatter().format(record))
    assert "ValueError: kaput" in out["exception"]


# ---------------------------------------------------------------------------
# drains (peewee insert_many mocked; real pool never touched)
# ---------------------------------------------------------------------------


def test_drain_request_logs_empty_returns_true_without_db(consumer_modules, monkeypatch):
    app_mod = consumer_modules.app
    db = _fake_db()
    monkeypatch.setattr(app_mod, "db", db)
    assert app_mod.drain_request_logs([]) is True
    db.connect.assert_not_called()


def test_drain_request_logs_inserts_and_closes(consumer_modules, monkeypatch):
    app_mod = consumer_modules.app
    db = _fake_db()
    monkeypatch.setattr(app_mod, "db", db)
    insert_mock = MagicMock(name="insert_many")
    monkeypatch.setattr(app_mod.RequestLog, "insert_many", insert_mock)
    payload = {"method": "GET", "path": "/abc123"}
    assert app_mod.drain_request_logs([(payload, _kafka_msg())]) is True
    insert_mock.assert_called_once_with([payload])
    insert_mock.return_value.execute.assert_called_once_with()
    db.connect.assert_called_once_with(reuse_if_open=True)
    db.close.assert_called_once_with()


def test_drain_request_logs_failure_returns_false(consumer_modules, monkeypatch):
    app_mod = consumer_modules.app
    db = _fake_db()
    monkeypatch.setattr(app_mod, "db", db)
    insert_mock = MagicMock(name="insert_many")
    insert_mock.return_value.execute.side_effect = RuntimeError("db down")
    monkeypatch.setattr(app_mod.RequestLog, "insert_many", insert_mock)
    assert app_mod.drain_request_logs([({"method": "GET"}, _kafka_msg())]) is False
    db.close.assert_called_once_with()


def test_drain_url_events_empty_returns_true_without_db(consumer_modules, monkeypatch):
    app_mod = consumer_modules.app
    db = _fake_db()
    monkeypatch.setattr(app_mod, "db", db)
    assert app_mod.drain_url_events([]) is True
    db.connect.assert_not_called()


def test_drain_url_events_inserts_and_closes(consumer_modules, monkeypatch):
    app_mod = consumer_modules.app
    db = _fake_db()
    monkeypatch.setattr(app_mod, "db", db)
    insert_mock = MagicMock(name="insert_many")
    monkeypatch.setattr(app_mod.Event, "insert_many", insert_mock)
    payload = {"url_id": 1, "user_id": 2, "event_type": "click"}
    assert app_mod.drain_url_events([(payload, _kafka_msg())]) is True
    insert_mock.assert_called_once_with([payload])
    insert_mock.return_value.execute.assert_called_once_with()
    db.connect.assert_called_once_with(reuse_if_open=True)
    db.close.assert_called_once_with()


def test_drain_url_events_failure_returns_false(consumer_modules, monkeypatch):
    app_mod = consumer_modules.app
    db = _fake_db()
    monkeypatch.setattr(app_mod, "db", db)
    insert_mock = MagicMock(name="insert_many")
    insert_mock.return_value.execute.side_effect = RuntimeError("db down")
    monkeypatch.setattr(app_mod.Event, "insert_many", insert_mock)
    assert app_mod.drain_url_events([({"url_id": 1}, _kafka_msg())]) is False
    db.close.assert_called_once_with()


# ---------------------------------------------------------------------------
# logging / signal / isolation (#164)
# ---------------------------------------------------------------------------


def test_setup_consumer_logging_repeat_is_noop(consumer_modules):
    first = consumer_modules.app.setup_consumer_logging()
    count = len(first.handlers)
    assert count >= 1
    second = consumer_modules.app.setup_consumer_logging()
    assert second is first
    assert len(second.handlers) == count


def test_handle_signal_stops_runner(consumer_modules, monkeypatch):
    monkeypatch.setattr(consumer_modules.app, "running", True)
    consumer_modules.app.handle_signal(signal.SIGTERM, None)
    assert consumer_modules.app.running is False


def test_consumer_import_does_not_shadow_flask_app(consumer_modules):
    import app as flask_app

    assert hasattr(flask_app, "create_app")
    assert sys.modules["app"] is flask_app
    assert sys.modules["consumer_app"] is consumer_modules.app


def test_consumer_pool_is_lazy_closed_at_import(consumer_modules):
    """Import must not open a DB connection (pool constructs lazily)."""
    assert consumer_modules.app.db.is_closed()
