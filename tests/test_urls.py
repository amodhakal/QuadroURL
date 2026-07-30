"""Tests for the /urls endpoints."""

from app.utils.events import flush_events


# ---------------------------------------------------------------------------
# POST /urls — Create a short URL
# ---------------------------------------------------------------------------

def test_create_url(client, sample_user):
    response = client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/page",
        "title": "My Page",
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data["original_url"] == "https://example.com/page"
    assert data["title"] == "My Page"
    assert data["user_id"] == sample_user.id
    assert data["is_active"] is True
    assert "short_code" in data
    assert len(data["short_code"]) == 6


def test_create_url_generates_unique_short_codes(client, sample_user):
    codes = set()
    for i in range(5):
        resp = client.post("/urls", json={
            "user_id": sample_user.id,
            "original_url": f"https://example.com/{i}",
            "title": f"Page {i}",
        })
        assert resp.status_code == 201
        codes.add(resp.get_json()["short_code"])
    assert len(codes) == 5


def test_create_url_missing_user_id(client):
    response = client.post("/urls", json={
        "original_url": "https://example.com",
        "title": "No user",
    })
    assert response.status_code == 400


def test_create_url_missing_original_url(client, sample_user):
    response = client.post("/urls", json={
        "user_id": sample_user.id,
        "title": "No URL",
    })
    assert response.status_code == 400


def test_create_url_missing_title(client, sample_user):
    response = client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com",
    })
    assert response.status_code == 400


def test_create_url_invalid_user_id_type(client):
    response = client.post("/urls", json={
        "user_id": "not_an_int",
        "original_url": "https://example.com",
        "title": "Bad ID",
    })
    assert response.status_code == 400


def test_create_url_nonexistent_user(client):
    response = client.post("/urls", json={
        "user_id": 99999,
        "original_url": "https://example.com",
        "title": "Ghost user",
    })
    assert response.status_code == 400


def test_create_url_empty_body(client):
    response = client.post("/urls", data="", content_type="application/json")
    assert response.status_code == 400


def test_create_url_records_event(client, sample_user):
    client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/tracked",
        "title": "Tracked",
    })
    flush_events()
    events_resp = client.get("/events")
    events = events_resp.get_json()
    assert len(events) >= 1
    assert events[-1]["event_type"] == "created"


# ---------------------------------------------------------------------------
# GET /urls — List URLs (paginated envelope)
# ---------------------------------------------------------------------------

def test_list_urls_empty(client):
    response = client.get("/urls")
    assert response.status_code == 200
    data = response.get_json()
    assert data["kind"] == "list"
    assert data["sample"] == []
    assert data["total_items"] == 0


def test_list_urls_returns_urls(client, sample_url):
    response = client.get("/urls")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["sample"]) >= 1
    assert data["sample"][0]["short_code"] == "abc123"


def test_list_urls_filter_by_user_id(client, sample_url, sample_user):
    response = client.get(f"/urls?user_id={sample_user.id}")
    assert response.status_code == 200
    data = response.get_json()
    assert all(u["user_id"] == sample_user.id for u in data["sample"])


def test_list_urls_filter_by_is_active(client, sample_url):
    response = client.get("/urls?is_active=true")
    assert response.status_code == 200
    data = response.get_json()
    assert all(u["is_active"] is True for u in data["sample"])


# ---------------------------------------------------------------------------
# GET /urls/<id> — Get a single URL
# ---------------------------------------------------------------------------

def test_get_url_by_id(client, sample_url):
    response = client.get(f"/urls/{sample_url.id}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["id"] == sample_url.id
    assert data["short_code"] == "abc123"


def test_get_url_not_found(client):
    response = client.get("/urls/99999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT /urls/<id> — Update URL
# ---------------------------------------------------------------------------

def test_update_url_title(client, sample_url):
    response = client.put(
        f"/urls/{sample_url.id}", json={"title": "New Title"}
    )
    assert response.status_code == 200
    assert response.get_json()["title"] == "New Title"


def test_update_url_deactivate(client, sample_url):
    response = client.put(
        f"/urls/{sample_url.id}", json={"is_active": False}
    )
    assert response.status_code == 200
    assert response.get_json()["is_active"] is False


def test_update_url_not_found(client):
    response = client.put("/urls/99999", json={"title": "Ghost"})
    assert response.status_code == 404


def test_update_url_no_body(client, sample_url):
    response = client.put(
        f"/urls/{sample_url.id}", data="", content_type="application/json"
    )
    assert response.status_code == 400


def test_update_url_records_event(client, sample_url):
    client.put(f"/urls/{sample_url.id}", json={"title": "Changed"})
    flush_events()
    events_resp = client.get("/events")
    events = events_resp.get_json()
    updated_events = [e for e in events if e["event_type"] == "updated"]
    assert len(updated_events) >= 1
