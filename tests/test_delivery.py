"""Delivery seams: real SQLite transactions, no external services."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from peewee import SqliteDatabase

from shared.schema import create_models
from shared.delivery_models import create_delivery_models
from shared.delivery import Poison, persist_rows, quarantine, schedule_milestones
from shared.delivery_worker import process_due, publish_replay, validate_destination


@pytest.fixture(autouse=True)
def clean_tables():
    yield


@pytest.fixture
def store():
    db = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
    core = create_models(db)
    delivery = create_delivery_models(db, core)
    db.connect()
    db.create_tables(
        [core.User, core.Url, core.Event, core.RequestLog, core.ApiKey, *vars(delivery).values()]
    )
    user = core.User.create(username="owner", email="owner@example.com")
    url = core.Url.create(
        user=user, short_code="abc", original_url="https://example.com", title="A"
    )
    yield SimpleNamespace(db=db, core=core, delivery=delivery, user=user, url=url)
    db.close()


def message(offset=1, value=b"broken", headers=None):
    return Mock(
        topic=Mock(return_value="url-events"),
        partition=Mock(return_value=0),
        offset=Mock(return_value=offset),
        value=Mock(return_value=value),
        key=Mock(return_value=b"1"),
        headers=Mock(return_value=headers),
    )


def test_quarantine_is_durable_and_unique(store):
    d = store.delivery
    for _ in range(2):
        with store.db.atomic():
            quarantine(message(), "invalid JSON", d)
    assert d.DeadLetter.select().count() == 1
    assert bytes(d.DeadLetter.get().payload) == b"broken"


def test_click_receipt_and_delivery_commit_together(store):
    s, d = store, store.delivery
    d.Subscription.create(url=s.url, destination="https://hooks.example.com/click", milestone=1)
    payload = dict(url_id=s.url.id, user_id=s.user.id, event_type="click", details="{}")

    def drain():
        persist_rows(
            [(payload, message())],
            s.db,
            s.core,
            d,
            s.core.Event,
            lambda row: schedule_milestones(s.core, d, row.url_id),
        )

    drain()
    drain()
    assert s.core.Event.select().count() == 1
    assert d.Delivery.select().count() == 1
    assert json.loads(d.Delivery.get().payload)["milestone"] == 1


def test_database_poison_isolated_from_good_rows(store):
    s, d = store, store.delivery
    bad = dict(url_id=999, user_id=s.user.id, event_type="click", details="{}")
    good = dict(bad, url_id=s.url.id)
    persist_rows(
        [(bad, message(1)), (good, message(2)), (Poison("JSON"), message(3))],
        s.db,
        s.core,
        d,
        s.core.Event,
    )
    assert s.core.Event.select().count() == 1
    assert d.DeadLetter.select().count() == 2


def test_outage_rolls_back_receipt_and_event(store):
    s, d = store, store.delivery
    payload = dict(url_id=s.url.id, user_id=s.user.id, event_type="click", details="{}")
    with pytest.raises(RuntimeError):
        persist_rows(
            [(payload, message())],
            s.db,
            s.core,
            d,
            s.core.Event,
            Mock(side_effect=RuntimeError("outage")),
        )
    assert s.core.Event.select().count() == d.Receipt.select().count() == 0
    assert d.DeadLetter.select().count() == 0


def test_worker_backoff_and_stable_identity(store):
    d = store.delivery
    letter = quarantine(message(), "bad", d)
    row = d.Replay.create(id="replay-1", dead_letter=letter, payload=b"{}", requested_by=1)
    send = Mock(side_effect=TimeoutError())
    assert process_due(d.Replay, send, now=100) == 1
    row = d.Replay.get_by_id(row.id)
    assert row.state == "pending" and row.attempts == 1 and row.available_at == 102
    assert process_due(d.Replay, send, now=101) == 0
    send.side_effect = None
    process_due(d.Replay, send, now=102)
    assert d.Replay.get().state == "sent"
    assert send.call_args.args[0].id == "replay-1"


def test_replay_requires_broker_ack_and_whitelisted_topic(store):
    d = store.delivery
    letter = quarantine(message(), "bad", d)
    replay = d.Replay.create(id="x", dead_letter=letter, requested_by=1)
    producer = Mock()
    with pytest.raises(RuntimeError):
        publish_replay(producer, replay)
    letter.topic = "admin-secret-topic"
    letter.save()
    replay = d.Replay.get()
    producer.reset_mock()
    with pytest.raises(ValueError):
        publish_replay(producer, replay)
    producer.produce.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user@example.com/",
        "https://example.com:444/",
        "https://example.com/#x",
    ],
)
def test_destinations_reject_unsafe_shapes(monkeypatch, url):
    monkeypatch.setenv("WEBHOOK_ALLOWED_URLS", url)
    with pytest.raises(ValueError):
        validate_destination(url)
