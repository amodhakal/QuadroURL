"""Schema-first validation contracts (#191) — hermetic SQLite, no services."""

import pytest
from flask import Flask, jsonify
from peewee import SqliteDatabase
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from app.models import ApiKey, Event, RequestLog, Url, User
from app.utils.schemas import (
    AnalyticsQuery,
    DeadLetterQuery,
    ExportQuery,
    ReplayRequest,
    WebhookCreate,
)


@pytest.fixture(autouse=True)
def clean_tables():
    yield


@pytest.fixture()
def schema_client(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBHOOK_ALLOWED_URLS", "https://hooks.example/ok")
    database = SqliteDatabase(str(tmp_path / "schema191.db"), pragmas={"foreign_keys": 1})
    from app.database import models as schema

    from app.routes import delivery as delivery_module

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
        from app.routes.analytics import analytics_bp
        from app.routes.delivery import delivery_bp
        from app.routes.exports import exports_bp
        from app.utils.auth import issue_api_key

        instance = Flask(__name__)
        instance.config.update(TESTING=True)
        for bp in (analytics_bp, delivery_bp, exports_bp):
            instance.register_blueprint(bp)
            instance.register_blueprint(bp, url_prefix="/api/v1", name=f"{bp.name}_v1")

        @instance.errorhandler(HTTPException)
        def error(exc):
            return jsonify(error=exc.description), exc.code

        owner = User.create(username="owner", email="owner@example.com")
        _, key = issue_api_key(owner.id)
        url = Url.create(
            user=owner,
            short_code="schema1",
            original_url="https://example.com",
            title="Example",
        )
        client = instance.test_client()
        client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {key}"
        yield client, owner, url
        database.close()


def test_webhook_schema_rejects_non_integer_milestones(monkeypatch):
    monkeypatch.setenv("WEBHOOK_ALLOWED_URLS", "https://hooks.example/ok")
    good = WebhookCreate.model_validate({"destination": "https://hooks.example/ok", "milestone": 5})
    assert good.milestone == 5
    for bad in (
        {"destination": "https://hooks.example/ok", "milestone": True},
        {"destination": "https://hooks.example/ok", "milestone": "5"},
        {"destination": "https://hooks.example/ok", "milestone": 0},
        {"destination": "https://hooks.example/ok", "milestone": -3},
        {"destination": "https://hooks.example/ok"},
        {"destination": "https://hooks.example/ok", "milestone": 5, "extra": 1},
        {"destination": "http://hooks.example/ok", "milestone": 5},
        {"destination": "https://evil.example/ok", "milestone": 5},
    ):
        with pytest.raises(ValidationError):
            WebhookCreate.model_validate(bad)


def test_replay_export_analytics_deadletter_schemas_reject_shapes():
    with pytest.raises(ValidationError):
        ReplayRequest.model_validate({"payload": [1, 2]})
    with pytest.raises(ValidationError):
        ReplayRequest.model_validate({"payload": {}, "extra": 1})
    assert ReplayRequest.model_validate({}).model_dump(exclude_unset=True) == {}
    for bad in (
        {"format": "xml"},
        {"limit": 0},
        {"limit": 1001},
        {"after_id": -1},
        {"user_id": 2},
    ):
        with pytest.raises(ValidationError):
            ExportQuery.model_validate(bad)
    for bad in ({"bucket": "hour"}, {"days": 0}, {"days": 366}, {"x": 1}):
        with pytest.raises(ValidationError):
            AnalyticsQuery.model_validate(bad)
    with pytest.raises(ValidationError):
        DeadLetterQuery.model_validate({"after": "bad"})
    assert DeadLetterQuery.model_validate({}).after == 0


def test_routes_reject_shapes_with_400(schema_client):
    client, owner, url = schema_client
    assert client.get("/api/v1/exports/users?format=xml").status_code == 400
    assert client.get("/api/v1/exports/users?limit=bad").status_code == 400
    assert client.get("/api/v1/exports/users?user_id=2").status_code == 400
    base = f"/api/v1/urls/{url.short_code}/analytics"
    assert client.get(f"{base}?bucket=hour").status_code == 400
    assert client.get(f"{base}?days=bad").status_code == 400
    assert client.get(f"{base}?x=1").status_code == 400
    webhooks = f"/api/v1/urls/{url.id}/webhooks"
    for payload in (
        {"destination": "https://hooks.example/ok", "milestone": True},
        {"destination": "https://hooks.example/ok", "milestone": "5"},
        {"destination": "https://hooks.example/ok"},
        {"destination": "https://hooks.example/ok", "milestone": 5, "extra": 1},
        {"destination": "https://evil.example/ok", "milestone": 5},
    ):
        assert client.post(webhooks, json=payload).status_code == 400
    created = client.post(
        webhooks, json={"destination": "https://hooks.example/ok", "milestone": 5}
    )
    assert created.status_code == 201
    assert created.json["milestone"] == 5
