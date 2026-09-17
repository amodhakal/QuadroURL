"""Graceful Failure tests — send bad inputs, verify clean JSON errors, no crashes.

Gold tier requirement: The app must return clean errors (JSON), not crash.
Every response to bad input should:
  - Have a valid HTTP error status code (4xx)
  - Return application/json content type
  - Contain an "error" key in the JSON body
  - Never expose a raw Python stack trace
"""

import json


# ---------------------------------------------------------------------------
# Helper: assert every error response is clean JSON
# ---------------------------------------------------------------------------


def assert_clean_json_error(response, expected_status):
    assert response.status_code == expected_status
    assert response.content_type == "application/json"
    data = response.get_json()
    assert data is not None, "Response body should be valid JSON"
    assert "error" in data, f"JSON error response should contain 'error' key, got: {data}"
    assert "Traceback" not in json.dumps(data), "Response must not contain a Python traceback"
    assert "<!DOCTYPE" not in json.dumps(data), "Response must not contain HTML"


# ---------------------------------------------------------------------------
# Global error handlers
# ---------------------------------------------------------------------------


def test_404_is_json(client):
    response = client.get("/this/route/does/not/exist")
    assert_clean_json_error(response, 404)


def test_405_method_not_allowed(client):
    response = client.delete("/health")
    assert response.status_code == 405


# ---------------------------------------------------------------------------
# POST /users — bad inputs
# ---------------------------------------------------------------------------


def test_create_user_integer_username(client):
    response = client.post("/users", json={"username": 12345, "email": "a@b.com"})
    assert response.status_code in (400, 422)
    assert response.content_type == "application/json"


def test_create_user_integer_email(client):
    response = client.post("/users", json={"username": "valid", "email": 12345})
    assert response.status_code in (400, 422)
    assert response.content_type == "application/json"


def test_create_user_empty_strings(client):
    response = client.post("/users", json={"username": "", "email": ""})
    assert response.status_code in (400, 422)
    assert response.content_type == "application/json"


def test_create_user_null_values(client):
    response = client.post("/users", json={"username": None, "email": None})
    assert response.status_code in (400, 422)
    assert response.content_type == "application/json"


def test_create_user_no_body(client):
    response = client.post("/users", content_type="application/json")
    assert_clean_json_error(response, 400)


def test_create_user_malformed_json(client):
    response = client.post("/users", data="{broken json", content_type="application/json")
    assert_clean_json_error(response, 400)


def test_create_user_wrong_content_type(client):
    response = client.post(
        "/users", data="username=test", content_type="application/x-www-form-urlencoded"
    )
    assert_clean_json_error(response, 400)


def test_create_user_extra_unknown_fields(client):
    response = client.post(
        "/users",
        json={"username": "extra", "email": "extra@test.com", "role": "admin", "age": 99},
    )
    # Should either succeed (ignoring extras) or fail cleanly
    assert response.content_type == "application/json"
    assert response.status_code in (201, 400, 422)


def test_create_user_duplicate_returns_json_error(client, sample_user):
    response = client.post(
        "/users",
        json={"username": sample_user.username, "email": "unique@test.com"},
    )
    assert_clean_json_error(response, 400)


# ---------------------------------------------------------------------------
# GET /users/<id> — bad inputs
# ---------------------------------------------------------------------------


def test_get_user_nonexistent_id(client):
    response = client.get("/users/99999")
    assert_clean_json_error(response, 404)


def test_get_user_string_id(client):
    """Flask's <int:user_id> should reject non-integers with a 404."""
    response = client.get("/users/notanumber")
    assert_clean_json_error(response, 404)


def test_get_user_negative_id(client):
    response = client.get("/users/-1")
    assert response.status_code in (400, 404)
    assert response.content_type == "application/json"


# ---------------------------------------------------------------------------
# PUT /users/<id> — bad inputs
# ---------------------------------------------------------------------------


def test_update_user_nonexistent(client):
    response = client.put("/users/99999", json={"username": "ghost"})
    assert_clean_json_error(response, 404)


def test_update_user_empty_json(owner_client, sample_user):
    response = owner_client.put(
        f"/users/{sample_user.id}", data="", content_type="application/json"
    )
    assert_clean_json_error(response, 400)


def test_update_user_malformed_json(owner_client, sample_user):
    response = owner_client.put(
        f"/users/{sample_user.id}", data="{bad", content_type="application/json"
    )
    assert_clean_json_error(response, 400)


# ---------------------------------------------------------------------------
# POST /users/bulk — bad inputs
# ---------------------------------------------------------------------------


def test_bulk_import_no_file_field(admin_client):
    response = admin_client.post("/users/bulk", content_type="multipart/form-data")
    assert_clean_json_error(response, 400)


def test_bulk_import_non_csv_file(admin_client):
    import io

    bad = (io.BytesIO(b"not,csv"), "data.txt")
    response = admin_client.post(
        "/users/bulk", data={"file": bad}, content_type="multipart/form-data"
    )
    assert_clean_json_error(response, 400)


def test_bulk_import_empty_csv(admin_client):
    import io

    empty = (io.BytesIO(b"username,email,created_at\n"), "empty.csv")
    response = admin_client.post(
        "/users/bulk", data={"file": empty}, content_type="multipart/form-data"
    )
    assert response.status_code == 200
    assert response.content_type == "application/json"
    assert response.get_json()["imported"] == 0


# ---------------------------------------------------------------------------
# POST /urls — bad inputs
# ---------------------------------------------------------------------------


def test_create_url_no_body(client):
    response = client.post("/urls", content_type="application/json")
    assert_clean_json_error(response, 400)


def test_create_url_malformed_json(client):
    response = client.post("/urls", data="{broken", content_type="application/json")
    assert_clean_json_error(response, 400)


def test_create_url_string_user_id(client):
    response = client.post(
        "/urls", json={"user_id": "not_int", "original_url": "https://x.com", "title": "T"}
    )
    assert_clean_json_error(response, 400)


def test_create_url_float_user_id(client):
    response = client.post(
        "/urls", json={"user_id": 1.5, "original_url": "https://x.com", "title": "T"}
    )
    assert_clean_json_error(response, 400)


def test_create_url_null_user_id(client):
    response = client.post(
        "/urls", json={"user_id": None, "original_url": "https://x.com", "title": "T"}
    )
    assert_clean_json_error(response, 400)


def test_create_url_nonexistent_user(admin_client):
    response = admin_client.post(
        "/urls", json={"user_id": 99999, "original_url": "https://x.com", "title": "T"}
    )
    assert_clean_json_error(response, 400)


def test_create_url_integer_original_url(owner_client, sample_user):
    response = owner_client.post(
        "/urls", json={"user_id": sample_user.id, "original_url": 12345, "title": "T"}
    )
    assert_clean_json_error(response, 400)


def test_create_url_integer_title(owner_client, sample_user):
    response = owner_client.post(
        "/urls", json={"user_id": sample_user.id, "original_url": "https://x.com", "title": 999}
    )
    assert_clean_json_error(response, 400)


def test_create_url_empty_strings(owner_client, sample_user):
    response = owner_client.post(
        "/urls", json={"user_id": sample_user.id, "original_url": "", "title": ""}
    )
    assert_clean_json_error(response, 400)


def test_create_url_missing_all_fields(client):
    response = client.post("/urls", json={})
    assert_clean_json_error(response, 400)


# ---------------------------------------------------------------------------
# GET /urls/<id> — bad inputs
# ---------------------------------------------------------------------------


def test_get_url_nonexistent(client):
    response = client.get("/urls/99999")
    assert_clean_json_error(response, 404)


def test_get_url_string_id(client):
    response = client.get("/urls/notanumber")
    assert_clean_json_error(response, 404)


# ---------------------------------------------------------------------------
# PUT /urls/<id> — bad inputs
# ---------------------------------------------------------------------------


def test_update_url_nonexistent(client):
    response = client.put("/urls/99999", json={"title": "Ghost"})
    assert_clean_json_error(response, 404)


def test_update_url_empty_json(owner_client, sample_url):
    response = owner_client.put(f"/urls/{sample_url.id}", data="", content_type="application/json")
    assert_clean_json_error(response, 400)


def test_update_url_malformed_json(owner_client, sample_url):
    response = owner_client.put(
        f"/urls/{sample_url.id}", data="{{bad", content_type="application/json"
    )
    assert_clean_json_error(response, 400)


# ---------------------------------------------------------------------------
# Redirect endpoints — bad inputs
# ---------------------------------------------------------------------------


def test_redirect_nonexistent_short_code(client):
    response = client.get("/urls/ZZZZZZ/redirect")
    assert_clean_json_error(response, 404)


def test_redirect_inactive_url(client, sample_user):
    """Deactivated URLs should return 404, not redirect."""
    from app.models.url import Url

    url = Url.create(
        user=sample_user,
        short_code="dead01",
        original_url="https://example.com",
        title="Inactive",
        is_active=False,
    )
    response = client.get(f"/urls/{url.short_code}/redirect")
    assert_clean_json_error(response, 404)


# ---------------------------------------------------------------------------
# POST /events — bad inputs
# ---------------------------------------------------------------------------


def test_create_event_no_body(client):
    response = client.post("/events", content_type="application/json")
    assert_clean_json_error(response, 400)


def test_create_event_string_url_id(client):
    response = client.post("/events", json={"url_id": "bad", "user_id": 1, "event_type": "click"})
    assert_clean_json_error(response, 400)


def test_create_event_string_user_id(client):
    response = client.post("/events", json={"url_id": 1, "user_id": "bad", "event_type": "click"})
    assert_clean_json_error(response, 400)


def test_create_event_missing_event_type(client):
    response = client.post("/events", json={"url_id": 1, "user_id": 1})
    assert_clean_json_error(response, 400)


def test_create_event_nonexistent_url(owner_client, sample_user):
    response = owner_client.post(
        "/events", json={"url_id": 99999, "user_id": sample_user.id, "event_type": "click"}
    )
    # Missing URLs use the same 404 response as inaccessible URLs.
    assert_clean_json_error(response, 404)


def test_create_event_nonexistent_user(admin_client, sample_url):
    response = admin_client.post(
        "/events", json={"url_id": sample_url.id, "user_id": 99999, "event_type": "click"}
    )
    assert_clean_json_error(response, 400)
