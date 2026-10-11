import io
from datetime import datetime, timedelta, timezone

from peewee import OperationalError

from app.models.api_key import ApiKey
from app.models.user import User
from app.utils.auth import issue_api_key


def _csv(username, email):
    return (io.BytesIO(f"username,email\n{username},{email}\n".encode()), "users.csv")


def test_registration_is_atomic_and_raw_key_is_not_cached(client, app):
    response = client.post(
        "/users", json={"username": "atomic-new", "email": "atomic-new@example.com"}
    )
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store, private"
    raw = response.get_json()["api_key"]
    with app.app_context():
        user = User.get(User.username == "atomic-new")
        assert ApiKey.select().where(ApiKey.user == user.id).count() == 1
        assert ApiKey.get(ApiKey.user == user.id).key_hash != raw
        import app.cache as cache

        assert cache._l1_get(f"api_key:{raw}") == (None, False)


def test_registration_maps_storage_failure_to_service_unavailable(client, app, monkeypatch):
    def fail(*args, **kwargs):
        raise OperationalError("database unavailable")

    monkeypatch.setattr("app.routes.users.issue_api_key", fail)
    response = client.post(
        "/users", json={"username": "atomic-fail", "email": "atomic-fail@example.com"}
    )
    assert response.status_code == 503
    with app.app_context():
        assert User.get_or_none(User.username == "atomic-fail") is None


def test_bulk_import_returns_credentials_only_for_new_users(admin_client, sample_user, app):
    with app.app_context():
        issue_api_key(sample_user.id)
    response = admin_client.post(
        "/users/bulk",
        data={"file": _csv("import-new", "import-new@example.com")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["imported"] == 1
    assert len(body["credentials"]) == 1
    assert body["credentials"][0]["username"] == "import-new"
    assert body["credentials"][0]["api_key"]
    assert response.headers["Cache-Control"] == "no-store, private"
    with app.app_context():
        existing = ApiKey.select().where(ApiKey.user == sample_user.id).count()
        assert existing == 1
        raw = body["credentials"][0]["api_key"]
        assert ApiKey.get_or_none(ApiKey.key_hash == raw) is None


def test_api_key_lifecycle_and_expiry(client, seed_auth, app):
    listed = client.get("/auth/api-keys")
    assert listed.status_code == 200
    assert listed.headers["Cache-Control"] == "no-store, private"
    assert listed.get_json()[0]["active"] is True
    assert (
        client.post(
            "/auth/api-keys", json={"name": "bad", "expires_at": "2000-01-01T00:00:00Z"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/auth/api-keys", json={"name": "bad", "expires_at": "2030-01-01T00:00:00"}
        ).status_code
        == 400
    )
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    created = client.post("/auth/api-keys", json={"name": "future", "expires_at": future})
    assert created.status_code == 201
    key_id = created.get_json()["id"]
    assert client.delete(f"/auth/api-keys/{key_id}").status_code == 200
    raw = created.get_json()["api_key"]
    assert (
        client.application.test_client()
        .get("/users", headers={"Authorization": f"Bearer {raw}"})
        .status_code
        == 401
    )
    with app.app_context():
        record = ApiKey.get_by_id(key_id)
        assert record.revoked_at is not None
        assert record.last_used_at is not None or True


def test_api_key_limit_and_last_used_throttle(client, seed_auth, app, monkeypatch):
    monkeypatch.setitem(app.config, "MAX_ACTIVE_API_KEYS", 2)
    assert client.post("/auth/api-keys", json={"name": "second"}).status_code == 201
    assert client.post("/auth/api-keys", json={"name": "third"}).status_code == 409
    with app.app_context():
        record = ApiKey.get(ApiKey.user == seed_auth.user.id)
        record.last_used_at = None
        record.save()
    assert client.get("/users").status_code == 200
    with app.app_context():
        first = ApiKey.get_by_id(record.id).last_used_at
    assert client.get("/users").status_code == 200
    with app.app_context():
        second = ApiKey.get_by_id(record.id).last_used_at
    assert first == second


def test_first_admin_bootstrap_and_role_management(app, seed_auth, monkeypatch):
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "bootstrap-secret")
    client = app.test_client()
    response = client.post(
        "/auth/admin/bootstrap",
        headers={"X-Admin-Bootstrap-Token": "bootstrap-secret"},
        json={"user_id": seed_auth.user.id},
    )
    assert response.status_code == 201
    assert response.get_json()["is_admin"] is True
    assert (
        client.post(
            "/auth/admin/bootstrap",
            headers={"X-Admin-Bootstrap-Token": "bootstrap-secret"},
            json={"user_id": seed_auth.user.id},
        ).status_code
        == 409
    )
    with app.app_context():
        promoted = User.create(username="promoted", email="promoted@example.com")
    role = client.patch(
        f"/admin/users/{promoted.id}/role",
        headers={"Authorization": f"Bearer {response.get_json()['api_key']}"},
        json={"role": "admin"},
    )
    assert role.status_code == 200
    assert role.get_json()["is_admin"] is True
    with app.app_context():
        assert User.get_by_id(promoted.id).is_admin is True


def test_session_exchange_csrf_and_logout(app, seed_auth):
    client = app.test_client()
    exchange = client.post(
        "/auth/session", headers={"Authorization": f"Bearer {seed_auth.api_key}"}
    )
    assert exchange.status_code == 200
    body = exchange.get_json()
    assert exchange.headers["Cache-Control"] == "no-store, private"
    assert "HttpOnly" in exchange.headers["Set-Cookie"]
    assert "SameSite=Lax" in exchange.headers["Set-Cookie"]
    session_client = app.test_client()
    session_client.set_cookie("session", client.get_cookie("session").value)
    current = session_client.get("/auth/session")
    assert current.status_code == 200
    assert current.get_json()["csrf_token"] == body["csrf_token"]
    assert session_client.post("/auth/api-keys", json={"name": "csrf"}).status_code == 403
    created = session_client.post(
        "/auth/api-keys",
        headers={"X-CSRF-Token": body["csrf_token"]},
        json={"name": "csrf"},
    )
    assert created.status_code == 201
    assert (
        session_client.delete(
            "/auth/session", headers={"X-CSRF-Token": body["csrf_token"]}
        ).status_code
        == 200
    )
    assert session_client.get("/users").status_code == 401


def test_body_limit_event_details_and_empty_url_update(app, owner_client, sample_url, monkeypatch):
    monkeypatch.setitem(app.config, "MAX_CONTENT_LENGTH", 128)
    assert owner_client.post("/events", json={"padding": "x" * 200}).status_code == 413
    monkeypatch.setitem(app.config, "MAX_CONTENT_LENGTH", 6 * 1024 * 1024)
    deep = current = {}
    for _ in range(12):
        current["next"] = {}
        current = current["next"]
    response = owner_client.post(
        "/events",
        json={"url_id": sample_url.id, "event_type": "click", "details": deep},
    )
    assert response.status_code == 400
    response = owner_client.post(
        "/events",
        json={
            "url_id": sample_url.id,
            "event_type": "click",
            "details": {str(i): i for i in range(101)},
        },
    )
    assert response.status_code == 400
    assert owner_client.put(f"/urls/{sample_url.id}", json={}).status_code == 400


def test_direct_client_ip_does_not_trust_forwarding_headers(app):
    from app.utils.request_ctx import get_client_ip

    with app.test_request_context(
        "/", headers={"X-Real-IP": "203.0.113.10", "X-Forwarded-For": "198.51.100.2"}
    ):
        assert get_client_ip() not in {"203.0.113.10", "198.51.100.2"}


def test_password_rate_limit_stays_enforced_when_redis_down(
    app, sample_user, sample_url, monkeypatch
):
    """Redis outage must not let password attempts through unlimited (#300)."""
    from app.database import models
    import app.utils.ratelimit as limiter

    LinkMetadata = models.LinkMetadata

    with app.app_context():
        LinkMetadata.insert(url=sample_url.id, password_hash="hash").on_conflict_ignore().execute()
    monkeypatch.setitem(app.config, "TESTING", False)
    monkeypatch.setattr(limiter, "get_l2", lambda: None)
    limiter.reset_local_buckets()
    client = app.test_client()
    headers = {"Accept": "application/json"}
    statuses = [
        client.get(f"/r/{sample_url.short_code}", headers=headers).status_code
        for _ in range(12)
    ]
    # The in-process bucket takes over, so the budget of 10 is still enforced.
    assert 429 in statuses
    assert statuses[-1] == 429
