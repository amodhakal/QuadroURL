"""Click analytics contract checks (#177) — hermetic SQLite, no services."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Event, Url, User
from app.utils.auth import issue_api_key


@pytest.fixture(autouse=True)
def clean_tables():
    """No integration DB fixture; the app fixture provides SQLite."""
    yield


@pytest.fixture()
def analytics_env(app):
    owner = User.create(username="analyst", email="analyst@example.com")
    intruder = User.create(username="intruder", email="intruder@example.com")
    url = Url.create(
        user=owner, short_code="stats1", original_url="https://example.com", title="Example"
    )
    _, key = issue_api_key(owner.id)
    _, intruder_key = issue_api_key(intruder.id)
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {key}"
    return client, url, key, intruder_key


def _click(url, days_ago, referrer="https://news.example", visitor="v1", ua=None):
    created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(tzinfo=None)
    details = {
        "short_code": url.short_code,
        "referrer": referrer,
        "user_agent": {"browser": "Chrome", "os": "macOS", "device": "desktop"},
        "visitor": visitor,
    }
    return Event.create(
        url=url,
        user=url.user,
        event_type="click",
        details=json.dumps(details),
        timestamp=created,
    )


def test_analytics_buckets_and_totals(analytics_env):
    client, url, key, _ = analytics_env
    _click(url, 1)
    _click(url, 0, visitor="v2")
    _click(url, 40)  # outside the 30-day window

    response = client.get(f"/api/v1/urls/{url.short_code}/analytics")
    assert response.status_code == 200
    body = response.json
    assert body["total_clicks"] == 2
    assert body["unique_visitors"] == 2
    assert sum(entry["clicks"] for entry in body["clicks_over_time"]) == 2
    assert body["browsers"] == {"Chrome": 2}
    assert body["devices"] == {"desktop": 2}
    assert body["top_referrers"][0]["referrer"] == "https://news.example"


def test_analytics_hides_foreign_codes(analytics_env):
    client, url, _, intruder_key = analytics_env
    _click(url, 0)
    response = client.get(
        f"/api/v1/urls/{url.short_code}/analytics",
        headers={"Authorization": f"Bearer {intruder_key}"},
    )
    assert response.status_code == 404


def test_analytics_rejects_unknown_params_and_bounds(analytics_env):
    client, url, _, _ = analytics_env
    base = f"/api/v1/urls/{url.short_code}/analytics"
    assert client.get(f"{base}?bucket=hour").status_code == 400
    assert client.get(f"{base}?days=366").status_code == 400
    assert client.get(f"{base}?days=bad").status_code == 400
    assert client.get(f"{base}?x=1").status_code == 400
    assert client.get("/api/v1/urls/missing/analytics").status_code == 404


def test_track_click_records_enriched_details(app, sample_user):
    """The redirect path publishes referrer/UA/visitor details for #177."""
    from unittest.mock import patch

    from app.models import Url
    from app.routes import urls as urls_module

    url = Url.create(
        user=sample_user,
        short_code="click01",
        original_url="https://example.com",
        title="T",
    )
    captured = {}

    def recorder(url_id, user_id, event_type, details):
        captured["payload"] = details

    test_client = app.test_client()
    with (
        patch.object(urls_module, "create_event", recorder),
        patch.object(urls_module, "stable_visitor_id", return_value="20260916:abc"),
    ):
        response = test_client.get(
            f"/urls/{url.short_code}/redirect",
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone) Safari",
                "Referer": "https://news.example",
            },
        )
    assert response.status_code == 302
    details = captured["payload"]
    assert details["referrer"] == "https://news.example"
    assert details["user_agent"]["os"] == "iOS"
    assert details["visitor"] == "20260916:abc"
