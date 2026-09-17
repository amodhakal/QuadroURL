"""Tests for the /users endpoints."""

import io


# ---------------------------------------------------------------------------
# POST /users — Create a single user
# ---------------------------------------------------------------------------


def test_create_user(client):
    response = client.post("/users", json={"username": "newuser", "email": "new@example.com"})
    assert response.status_code == 201
    data = response.get_json()
    assert data["username"] == "newuser"
    assert data["email"] == "new@example.com"
    assert "id" in data
    assert "created_at" in data


def test_create_user_missing_username(client):
    response = client.post("/users", json={"email": "no_name@example.com"})
    assert response.status_code == 400


def test_create_user_missing_email(client):
    response = client.post("/users", json={"username": "no_email"})
    assert response.status_code == 400


def test_create_user_empty_body(client):
    response = client.post("/users", data="", content_type="application/json")
    assert response.status_code == 400


def test_create_user_invalid_json(client):
    response = client.post("/users", data="not json", content_type="application/json")
    assert response.status_code == 400


def test_create_user_duplicate_username(client, sample_user):
    response = client.post(
        "/users", json={"username": sample_user.username, "email": "other@example.com"}
    )
    assert response.status_code == 400


def test_create_user_duplicate_email(client, sample_user):
    response = client.post("/users", json={"username": "otheruser", "email": sample_user.email})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# GET /users — List users (paginated envelope)
# ---------------------------------------------------------------------------


def test_list_users_empty(admin_client):
    response = admin_client.get("/users")
    assert response.status_code == 200
    data = response.get_json()
    assert data["kind"] == "list"
    # The auto-auth seed user (conftest) is infrastructure, not fixture data.
    rows = [u for u in data["sample"] if u["username"] != "authseed"]
    assert rows == []


def test_list_users_returns_users(admin_client, sample_user):
    response = admin_client.get("/users")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["sample"]) >= 1
    by_name = {u["username"]: u for u in data["sample"]}
    assert by_name["testuser"]["id"] == sample_user.id


def test_list_users_pagination(admin_client):
    # 4 created + 1 authseed row = 5; last page is partial (proves it).
    for i in range(4):
        admin_client.post("/users", json={"username": f"user{i}", "email": f"user{i}@test.com"})

    response = admin_client.get("/users?page=1&per_page=2")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["sample"]) == 2

    response = admin_client.get("/users?page=3&per_page=2")
    data = response.get_json()
    assert len(data["sample"]) == 1


# ---------------------------------------------------------------------------
# GET /users/<id> — Get a single user
# ---------------------------------------------------------------------------


def test_get_user_by_id(owner_client, sample_user):
    response = owner_client.get(f"/users/{sample_user.id}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["id"] == sample_user.id
    assert data["username"] == "testuser"


def test_get_user_not_found(client):
    response = client.get("/users/99999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT /users/<id> — Update user
# ---------------------------------------------------------------------------


def test_update_user_username(owner_client, sample_user):
    response = owner_client.put(f"/users/{sample_user.id}", json={"username": "updated_name"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["username"] == "updated_name"


def test_update_user_email(owner_client, sample_user):
    response = owner_client.put(f"/users/{sample_user.id}", json={"email": "updated@example.com"})
    assert response.status_code == 200
    assert response.get_json()["email"] == "updated@example.com"


def test_update_user_not_found(client):
    response = client.put("/users/99999", json={"username": "ghost"})
    assert response.status_code == 404


def test_update_user_no_body(owner_client, sample_user):
    response = owner_client.put(
        f"/users/{sample_user.id}", data="", content_type="application/json"
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /users/bulk — CSV bulk import
# ---------------------------------------------------------------------------


def test_bulk_import_users(admin_client, users_csv):
    file_data, filename = users_csv
    response = admin_client.post(
        "/users/bulk",
        data={"file": (file_data, filename)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["imported"] == 2


def test_bulk_import_no_file(admin_client):
    response = admin_client.post("/users/bulk", content_type="multipart/form-data")
    assert response.status_code == 400


def test_bulk_import_wrong_file_type(admin_client):
    bad_file = (io.BytesIO(b"some data"), "data.txt")
    response = admin_client.post(
        "/users/bulk",
        data={"file": bad_file},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_bulk_import_replaces_existing_users(admin_client, sample_user, users_csv):
    """Bulk import is additive (non-destructive): existing users are kept."""
    file_data, filename = users_csv
    response = admin_client.post(
        "/users/bulk",
        data={"file": (file_data, filename)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200

    list_response = admin_client.get("/users")
    data = list_response.get_json()
    usernames = {u["username"] for u in data["sample"]}
    assert "testuser" in usernames
    assert "alice" in usernames


def test_bulk_import_wrong_content_type(admin_client):
    """Bulk import without multipart/form-data content type should be 415."""
    response = admin_client.post(
        "/users/bulk",
        data="username,email\nalice,alice@x.com\n",
        content_type="application/json",
    )
    assert response.status_code == 415


def test_get_user_by_id_cache_miss(app, owner_client, sample_user):
    """A user created directly in the DB (not via POST) is not in cache,
    so GET must fetch from DB, set it, and return it."""
    from app.cache import get_l2

    user = sample_user

    # Ensure a real cache miss (L1 and L2 cleared for this key).
    import app.cache as cache

    cache._l1.clear()
    r = get_l2()
    if r:
        r.delete(f"user:{user.id}")

    response = owner_client.get(f"/users/{user.id}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["id"] == user.id
    assert data["username"] == user.username
    assert data["email"] == user.email


def test_delete_user(owner_client, sample_user):
    response = owner_client.delete(f"/users/{sample_user.id}")
    assert response.status_code == 200


def test_delete_user_nonexistent(client):
    response = client.delete("/users/99999")
    # Ownership is checked before existence for ordinary users.
    assert response.status_code == 404


def test_get_user_cached_db_fetch_when_cache_misses(app, owner_client, sample_user, monkeypatch):
    """When the cache layer returns None for an existing user, get_user_cached
    falls through to User.get_by_id and returns from the DB (lines 88-90)."""
    import app.routes.users as users_module

    monkeypatch.setattr(users_module, "get_user", lambda user_id: None)
    response = owner_client.get(f"/users/{sample_user.id}")
    assert response.status_code == 200
    assert response.get_json()["id"] == sample_user.id
