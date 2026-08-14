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


def test_create_url_short_code_collision_returns_500(
    app, client, sample_user, monkeypatch
):
    """Exhausting short-code retries should return a 500 JSON error."""
    import app.routes.urls as urls_module
    from peewee import IntegrityError

    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)

    def always_collide(**kwargs):
        raise IntegrityError("Unique constraint violated")

    monkeypatch.setattr(urls_module.Url, "create", always_collide)
    response = client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/collision",
        "title": "Collision",
    })
    assert response.status_code == 500
    assert "unique short code" in response.get_json().get("error", "")


def test_list_urls_filter_by_id(client, sample_user):
    r1 = client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/one",
        "title": "One",
    })
    r2 = client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/two",
        "title": "Two",
    })
    id1 = r1.get_json()["id"]
    id2 = r2.get_json()["id"]
    assert id1 != id2
    response = client.get(f"/urls?id={id1}")
    assert response.status_code == 200
    sample = response.get_json()["sample"]
    assert len(sample) == 1
    assert sample[0]["id"] == id1


def test_list_urls_filter_by_short_code(client, sample_user):
    r1 = client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/a",
        "title": "A",
    })
    r2 = client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/b",
        "title": "B",
    })
    code = r2.get_json()["short_code"]
    response = client.get(f"/urls?short_code={code}")
    assert response.status_code == 200
    sample = response.get_json()["sample"]
    assert len(sample) == 1
    assert sample[0]["short_code"] == code


def test_list_urls_filter_by_original_url(client, sample_user):
    client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/match",
        "title": "Match",
    })
    client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/other",
        "title": "Other",
    })
    response = client.get(
        "/urls?original_url=https://example.com/match"
    )
    assert response.status_code == 200
    sample = response.get_json()["sample"]
    assert len(sample) == 1
    assert sample[0]["original_url"] == "https://example.com/match"


def test_get_url_unexpected_exception_returns_500(app, client, monkeypatch):
    import app.routes.urls as urls_module

    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    monkeypatch.setattr(urls_module, "get_url", lambda url_id: None)
    monkeypatch.setattr(
        urls_module.Url, "get_by_id", lambda url_id: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
    )
    response = client.get("/urls/1")
    assert response.status_code == 500
    assert "Internal server error" in response.get_json().get("error", "")


def test_delete_url_existing(client, sample_url):
    response = client.delete(f"/urls/{sample_url.id}")
    assert response.status_code == 200
    assert response.get_json() == {}
    remaining = client.get("/urls").get_json()["sample"]
    assert all(u["id"] != sample_url.id for u in remaining)


def test_delete_url_nonexistent(client):
    response = client.delete("/urls/99999")
    assert response.status_code == 200
    assert response.get_json() == {}


def test_redirect_short_code_success(client, sample_user):
    resp = client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": "https://example.com/redirect-me",
        "title": "Redirect",
    })
    short_code = resp.get_json()["short_code"]
    response = client.get(f"/urls/{short_code}/redirect")
    assert response.status_code == 302
    assert response.headers["Location"] == "https://example.com/redirect-me"

    flush_events()
    events = client.get("/events").get_json()
    clicks = [e for e in events if e["event_type"] == "click"]
    assert len(clicks) == 1
    assert clicks[0]["url_id"] == resp.get_json()["id"]


def test_legacy_redirect_success(client, sample_user):
    original = "https://example.com/legacy"
    resp = client.post("/urls", json={
        "user_id": sample_user.id,
        "original_url": original,
        "title": "Legacy",
    })
    short_code = resp.get_json()["short_code"]
    response = client.get(f"/r/{short_code}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["url"] == original
    assert data["short_code"] == short_code

    flush_events()
    events = client.get("/events").get_json()
    clicks = [e for e in events if e["event_type"] == "click"]
    assert len(clicks) == 1


def test_get_url_db_fetch_populates_cache(app, client, sample_user):
    """GET /urls/<id> for a URL not present in cache should fetch it from the
    DB, populate the cache, and return it (covers lines 176-179)."""
    from app.models.url import Url

    with app.app_context():
        url = Url.create(
            user=sample_user,
            short_code="dbft01",
            original_url="https://example.com/db",
            title="From DB",
            is_active=True,
        )

    import app.cache as cache
    cache._l1.clear()
    r = cache.get_l2()
    if r:
        r.delete(f"url:{url.id}")

    response = client.get(f"/urls/{url.id}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["id"] == url.id
    assert data["short_code"] == "dbft01"


def test_legacy_redirect_not_found(client):
    response = client.get("/r/NOPE01")
    assert response.status_code == 404


def test_legacy_redirect_inactive(app, client, sample_user):
    from app.models.url import Url

    with app.app_context():
        Url.create(
            user=sample_user,
            short_code="lgyi01",
            original_url="https://example.com",
            title="Inactive legacy",
            is_active=False,
        )

    response = client.get("/r/lgyi01")
    assert response.status_code == 404



def test_get_url_cached_db_fetch_when_cache_misses(app, client, sample_url, monkeypatch):
    """When the cache layer returns None for an existing URL, get_url_cached
    falls through to Url.get_by_id and returns from the DB (lines 176-179)."""
    import app.routes.urls as urls_module

    monkeypatch.setattr(urls_module, "get_url", lambda url_id: None)
    response = client.get(f"/urls/{sample_url.id}")
    assert response.status_code == 200
    assert response.get_json()["id"] == sample_url.id
