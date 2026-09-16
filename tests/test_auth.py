"""Tests for bearer-key authentication, phase 1 (#99)."""

import pytest

UNAUTHENTICATED = object()


@pytest.fixture()
def raw_client(app):
    """A test client with no credentials (proves the 401 boundary)."""
    return app.test_client()


def test_missing_key_rejected(raw_client):
    r = raw_client.get("/users")
    assert r.status_code == 401
    assert r.get_json()["error"] == "Missing or invalid API key"


def test_malformed_header_rejected(raw_client):
    for header in ("Token abc123", "Bearer ", "Bearer"):
        r = raw_client.get("/users", headers={"Authorization": header})
        assert r.status_code == 401, header


def test_unknown_key_rejected(raw_client):
    r = raw_client.get("/users", headers={"Authorization": "Bearer " + "x" * 43})
    assert r.status_code == 401


def test_valid_key_grants_access(client):
    r = client.get("/users")
    assert r.status_code == 200


def test_registration_issues_working_key(raw_client):
    r = raw_client.post("/users", json={"username": "newbie", "email": "newbie@example.com"})
    assert r.status_code == 201
    raw = r.get_json()["api_key"]
    assert isinstance(raw, str) and len(raw) >= 32
    r2 = raw_client.get("/users", headers={"Authorization": f"Bearer {raw}"})
    assert r2.status_code == 200


def test_key_rotation_mints_second_working_key(client, seed_auth):
    r = client.post("/auth/api-keys", json={"name": "laptop"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["user_id"] == seed_auth.user.id
    assert body["name"] == "laptop"
    assert body["api_key"] != seed_auth.api_key
    # Old key still works; new key works too.
    assert client.get("/users").status_code == 200
    test_client = client.application.test_client()
    r2 = test_client.get("/users", headers={"Authorization": f"Bearer {body['api_key']}"})
    assert r2.status_code == 200


def test_rotation_requires_auth(raw_client):
    assert raw_client.post("/auth/api-keys", json={}).status_code == 401


def test_public_endpoints_need_no_key(raw_client, client, seed_auth):
    assert raw_client.get("/health").status_code == 200
    assert raw_client.get("/prometheus-metrics").status_code == 200
    # The redirect product surface stays public.
    r = client.post(
        "/urls",
        json={
            "user_id": seed_auth.user.id,
            "original_url": "https://example.com",
            "title": "Example",
        },
    )
    assert r.status_code in (200, 201, 202), r.get_json()
    short_code = r.get_json().get("short_code")
    if short_code:
        redirect = raw_client.get(f"/r/{short_code}", follow_redirects=False)
        assert redirect.status_code == 200
        assert redirect.get_json()["url"] == "https://example.com"


def test_stored_form_is_digest_not_secret(app, seed_auth):
    from app.models.api_key import ApiKey

    with app.app_context():
        record = ApiKey.get_by_id(ApiKey.select().where(ApiKey.user == seed_auth.user.id).get().id)
        assert record.key_hash != seed_auth.api_key
        assert len(record.key_hash) == 64
