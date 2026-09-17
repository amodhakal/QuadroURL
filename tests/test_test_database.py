"""Regression checks for the shared test database fixtures."""

from app.database import db
from app.models.request_log import RequestLog
from app.utils.events import flush_events


def test_request_logging_uses_initialized_schema(client):
    response = client.get("/users")

    assert response.status_code == 200
    assert RequestLog.select().where(RequestLog.path == "/users").exists()


def test_migrated_tables_exist(app):
    from migrations_runner import applied, discover

    assert {
        "user",
        "url",
        "event",
        "apikey",
        "requestlog",
        "linkmetadata",
        "subscription",
        "delivery",
        "receipt",
        "replay",
        "deadletter",
    } <= set(db.get_tables())
    assert applied(db.obj) == {name for name, _ in discover()}


def test_click_event_persists_with_delivery_schema(client, sample_url):
    from app.models.event import Event

    response = client.get(f"/urls/{sample_url.short_code}/redirect")
    flush_events()

    assert response.status_code == 302
    assert Event.select().where(Event.url == sample_url, Event.event_type == "click").exists()
