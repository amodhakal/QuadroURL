"""Shared schema factory contract; isolated from integration services."""

import pytest
from peewee import SqliteDatabase


@pytest.fixture(autouse=True)
def clean_tables():
    """No app/database fixture is needed for these factory tests."""
    yield


def test_app_and_consumer_share_schema_without_sharing_connection():
    from shared.schema import create_models

    app_db = SqliteDatabase(":memory:")
    consumer_db = SqliteDatabase(":memory:")
    app = create_models(app_db)
    consumer = create_models(consumer_db)
    for name in ("User", "Url", "Event", "RequestLog", "ApiKey"):
        app_model = getattr(app, name)
        consumer_model = getattr(consumer, name)
        assert app_model is not consumer_model
        assert app_model._meta.database is app_db
        assert consumer_model._meta.database is consumer_db
        assert app_model._meta.table_name == consumer_model._meta.table_name
        assert {
            name: (type(field), field.null, field.unique, getattr(field, "max_length", None))
            for name, field in app_model._meta.columns.items()
        } == {
            name: (type(field), field.null, field.unique, getattr(field, "max_length", None))
            for name, field in consumer_model._meta.columns.items()
        }

    with consumer_db:
        consumer_db.create_tables([consumer.User, consumer.Url, consumer.Event])
        user = consumer.User.create(username="owner", email="owner@example.com")
        url = consumer.Url.create(
            user_id=user.id,
            short_code="abc123",
            original_url="https://example.com",
            title="Example",
        )
        assert url.is_active is True
        assert url.expires_at is None
        event = consumer.Event.create(
            url_id=url.id, user_id=user.id, event_type="click", details="{}"
        )
        assert event.url_id == url.id
        assert event.user_id == user.id


def test_consumer_write_is_readable_through_authenticated_api(monkeypatch, tmp_path):
    import importlib
    import sys
    from pathlib import Path
    from unittest.mock import MagicMock

    from flask import Flask
    from app.database import db as app_db, models as app_models
    from app.routes import urls
    from app.utils.auth import issue_api_key

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "consumer"))
    # The standalone entry point has local imports; restore module state afterwards.
    names = ("models", "config", "url_create_handler")
    saved = {name: sys.modules.get(name) for name in names}
    original_db = app_db.obj
    database = SqliteDatabase(str(tmp_path / "shared.db"), pragmas={"foreign_keys": 1})
    try:
        consumer = importlib.import_module("models")
        handler = importlib.import_module("url_create_handler")
        app_db.initialize(database)
        with database:
            database.create_tables([app_models.User, app_models.Url, app_models.ApiKey])
            user = app_models.User.create(username="smoke", email="smoke@example.com")
            _, key = issue_api_key(user.id)
            consumer_tables = [consumer.User, consumer.Url]
            with database.bind_ctx(consumer_tables):
                ok, events = handler.handle_url_create_batch(
                    [
                        {
                            "request_id": "smoke-create",
                            "user_id": user.id,
                            "original_url": "https://example.com/smoke",
                            "title": "Shared schema",
                        }
                    ],
                    database,
                    MagicMock(),
                )
            assert ok and len(events) == 1
            url_id = events[0]["url_id"]
            monkeypatch.setattr(urls, "get_url", lambda _: None)
            monkeypatch.setattr(urls, "set_url", lambda *_: None)
            api = Flask(__name__)
            api.config["TESTING"] = True
            api.register_blueprint(urls.urls_bp)
            response = api.test_client().get(
                f"/urls/{url_id}", headers={"Authorization": f"Bearer {key}"}
            )
            assert response.status_code == 200
            assert response.json["original_url"] == "https://example.com/smoke"
            assert response.json["user_id"] == user.id
            assert response.json["is_active"] is True
    finally:
        app_db.initialize(original_db)
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
