"""Tests for the /events endpoint."""

from app.utils.events import flush_events


def test_list_events_empty(client):
    response = client.get("/events")
    assert response.status_code == 200
    assert response.get_json() == []


def test_list_events_after_url_creation(client, sample_user):
    """Creating a URL should produce a 'created' event."""
    client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com",
        "title": "Test",
    })

    flush_events()
    response = client.get("/events")
    assert response.status_code == 200
    events = response.get_json()
    assert len(events) == 1
    assert events[0]["event_type"] == "created"
    assert events[0]["user_id"]["id"] == sample_user.id


def test_event_has_required_fields(client, sample_user):
    client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com",
        "title": "Fields test",
    })

    flush_events()
    events = client.get("/events").get_json()
    event = events[0]
    assert "id" in event
    assert "url_id" in event
    assert "user_id" in event
    assert "event_type" in event
    assert "timestamp" in event
    assert "details" in event


def test_event_details_is_dict(client, sample_user):
    """The details field should be parsed from JSON string into a dict."""
    client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/detail",
        "title": "Detail test",
    })

    flush_events()
    events = client.get("/events").get_json()
    assert isinstance(events[0]["details"], dict)
    assert "short_code" in events[0]["details"]
    assert "original_url" in events[0]["details"]


def test_update_url_produces_event(client, sample_user):
    """Updating a URL title should produce an 'updated' event."""
    url_resp = client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com",
        "title": "Before",
    })
    url_id = url_resp.get_json()["id"]

    client.put(f"/urls/{url_id}", json={"title": "After"})

    flush_events()
    events = client.get("/events").get_json()
    updated = [e for e in events if e["event_type"] == "updated"]
    assert len(updated) == 1
    assert updated[0]["details"]["field"] == "title"
    assert updated[0]["details"]["new_value"] == "After"
