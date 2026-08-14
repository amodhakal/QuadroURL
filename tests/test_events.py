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


def test_format_event_parses_details(app, sample_url, sample_user):
    from app.models.event import Event
    from app.routes.events import format_event

    with app.app_context():
        event = Event.create(
            url_id=sample_url.id,
            user_id=sample_user.id,
            event_type="click",
            details='{"a":1}',
        )
        result = format_event(event)
        assert result["url_id"] == sample_url.id
        assert result["user_id"] == {"id": sample_user.id}
        assert result["details"] == {"a": 1}


def test_format_event_invalid_details_falls_back_to_empty(app, sample_url, sample_user):
    from app.models.event import Event
    from app.routes.events import format_event

    with app.app_context():
        event = Event.create(
            url_id=sample_url.id,
            user_id=sample_user.id,
            event_type="click",
            details="not valid json",
        )
        result = format_event(event)
        assert result["details"] == {}


def test_list_events_filter_by_url_id(app, client, sample_user):
    from app.models.event import Event
    from app.models.url import Url

    with app.app_context():
        url1 = Url.create(
            user=sample_user,
            short_code="f1lt1",
            original_url="https://example.com/1",
            title="One",
            is_active=True,
        )
        url2 = Url.create(
            user=sample_user,
            short_code="f1lt2",
            original_url="https://example.com/2",
            title="Two",
            is_active=True,
        )
        Event.create(url_id=url1.id, user_id=sample_user.id, event_type="click", details="{}")
        Event.create(url_id=url2.id, user_id=sample_user.id, event_type="created", details="{}")

    response = client.get(f"/events?url_id={url1.id}")
    assert response.status_code == 200
    events = response.get_json()
    assert len(events) == 1
    assert events[0]["url_id"] == url1.id
    assert events[0]["event_type"] == "click"


def test_list_events_filter_by_user_id(app, client, sample_user):
    from app.models.event import Event
    from app.models.url import Url
    from app.models.user import User

    with app.app_context():
        other = User.create(username="other", email="other@example.com")
        url1 = Url.create(
            user=sample_user,
            short_code="f2lt1",
            original_url="https://example.com/1",
            title="One",
            is_active=True,
        )
        url2 = Url.create(
            user=other,
            short_code="f2lt2",
            original_url="https://example.com/2",
            title="Two",
            is_active=True,
        )
        Event.create(url_id=url1.id, user_id=sample_user.id, event_type="click", details="{}")
        Event.create(url_id=url2.id, user_id=other.id, event_type="click", details="{}")

    response = client.get(f"/events?user_id={sample_user.id}")
    assert response.status_code == 200
    events = response.get_json()
    assert len(events) == 1
    assert events[0]["user_id"]["id"] == sample_user.id


def test_list_events_filter_by_event_type(app, client, sample_user):
    from app.models.event import Event
    from app.models.url import Url

    with app.app_context():
        url = Url.create(
            user=sample_user,
            short_code="f3lt1",
            original_url="https://example.com/1",
            title="One",
            is_active=True,
        )
        Event.create(url_id=url.id, user_id=sample_user.id, event_type="click", details="{}")
        Event.create(url_id=url.id, user_id=sample_user.id, event_type="created", details="{}")

    response = client.get("/events?event_type=click")
    assert response.status_code == 200
    events = response.get_json()
    assert len(events) == 1
    assert events[0]["event_type"] == "click"


def test_list_events_malformed_details(app, client, sample_url, sample_user):
    from app.models.event import Event

    with app.app_context():
        Event.create(
            url_id=sample_url.id,
            user_id=sample_user.id,
            event_type="click",
            details="not json",
        )

    response = client.get("/events")
    assert response.status_code == 200
    events = response.get_json()
    assert len(events) == 1
    assert events[0]["details"] == {}


def test_create_event_success(client, sample_url, sample_user):
    response = client.post("/events", json={
        "url_id": sample_url.id,
        "user_id": sample_user.id,
        "event_type": "click",
        "details": {"foo": "bar"},
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data["url_id"] == sample_url.id
    assert data["user_id"] == {"id": sample_user.id}
    assert data["event_type"] == "click"
    assert data["details"] == {"foo": "bar"}


def test_create_event_details_must_be_object(client, sample_url, sample_user):
    response = client.post("/events", json={
        "url_id": sample_url.id,
        "user_id": sample_user.id,
        "event_type": "click",
        "details": "not an object",
    })
    assert response.status_code == 400
    assert response.get_json().get("error") == "details must be an object"
