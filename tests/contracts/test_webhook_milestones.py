"""Click-milestone webhook journey (#184) — hermetic SQLite, no services.

Owner registers a milestone subscription through the API; click accumulation
schedules exactly one delivery when the milestone is reached (never before,
never duplicated past it); disabling the subscription stops further sends;
the worker marks the delivery sent. The HTTP POST itself stays mocked — no
real webhook traffic.
"""

import json
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify
from peewee import SqliteDatabase
from werkzeug.exceptions import HTTPException

from app.models import ApiKey, Event, RequestLog, Url, User
from shared.delivery import schedule_milestones
from shared.delivery_worker import process_due


@pytest.fixture(autouse=True)
def clean_tables():
    yield


@pytest.fixture()
def milestone_client(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBHOOK_ALLOWED_URLS", "https://hooks.example/milestone")
    database = SqliteDatabase(str(tmp_path / "miles184.db"), pragmas={"foreign_keys": 1})
    from app.database import models as schema
    from app.routes import delivery as delivery_module
    from app.utils.auth import issue_api_key

    monkeypatch.setattr(delivery_module, "db", database)
    delivery = delivery_module.delivery
    tables = [
        User,
        Url,
        Event,
        ApiKey,
        RequestLog,
        schema.LinkMetadata,
        delivery.DeadLetter,
        delivery.Receipt,
        delivery.Replay,
        delivery.Subscription,
        delivery.Delivery,
    ]
    with database.bind_ctx(tables):
        database.create_tables(tables)
        from app.routes.delivery import delivery_bp

        instance = Flask(__name__)
        instance.config.update(TESTING=True)
        instance.register_blueprint(delivery_bp)
        instance.register_blueprint(delivery_bp, url_prefix="/api/v1", name="delivery_v1")

        @instance.errorhandler(HTTPException)
        def error(exc):
            return jsonify(error=exc.description), exc.code

        owner = User.create(username="milestone", email="milestone@example.com")
        _, key = issue_api_key(owner.id)
        url = Url.create(
            user=owner,
            short_code="miles1",
            original_url="https://example.com",
            title="Example",
        )
        client = instance.test_client()
        client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {key}"
        yield client, owner, url, delivery
        database.close()


def _click(owner, url, delivery):
    Event.create(url=url, user=owner, event_type="click", details=json.dumps({}))
    schedule_milestones(SimpleNamespace(Event=Event), delivery, url.id)


def test_milestone_fires_once_and_only_at_threshold(milestone_client):
    client, owner, url, delivery = milestone_client
    created = client.post(
        f"/api/v1/urls/{url.id}/webhooks",
        json={"destination": "https://hooks.example/milestone", "milestone": 2},
    )
    assert created.status_code == 201

    _click(owner, url, delivery)
    assert delivery.Delivery.select().count() == 0  # below threshold: silence

    _click(owner, url, delivery)
    rows = list(delivery.Delivery.select())
    assert len(rows) == 1
    assert json.loads(rows[0].payload)["milestone"] == 2
    assert rows[0].state == "pending"

    _click(owner, url, delivery)
    assert delivery.Delivery.select().count() == 1  # past threshold: no duplicate

    sent = []
    assert process_due(delivery.Delivery, sent.append) == 1
    assert delivery.Delivery.get().state == "sent"
    assert json.loads(sent[0].payload)["type"] == "click.milestone"

    listed = client.get(f"/api/v1/urls/{url.id}/webhook-deliveries")
    assert listed.status_code == 200
    assert listed.json[0]["state"] == "sent"


def test_disabled_subscription_schedules_nothing(milestone_client):
    client, owner, url, delivery = milestone_client
    created = client.post(
        f"/api/v1/urls/{url.id}/webhooks",
        json={"destination": "https://hooks.example/milestone", "milestone": 1},
    )
    subscription_id = created.json["id"]
    assert client.delete(f"/api/v1/urls/{url.id}/webhooks/{subscription_id}").status_code == 204

    _click(owner, url, delivery)
    assert delivery.Delivery.select().count() == 0  # disabled: silence
