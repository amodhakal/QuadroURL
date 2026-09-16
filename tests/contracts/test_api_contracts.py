"""Ownership, admin, limiter, and OpenAPI contract checks on SQLite, no services."""

import json
from unittest.mock import Mock


from app.models import User, Url
from app.utils.auth import issue_api_key


def actor(app, name):
    with app.app_context():
        user = User.create(username=name, email=f"{name}@example.com")
        _, key = issue_api_key(user.id)
    return user, {"Authorization": f"Bearer {key}"}


def seed(app, user, **overrides):
    from app import cache

    with app.app_context():
        row = Url.create(
            user=user,
            title="t",
            short_code=overrides.pop("short_code", "abc123"),
            original_url="https://example.com",
            **overrides,
        )
        cache._l1.clear()
        return row


def test_pagination_against_real_routes(app):
    user, headers = actor(app, "owner")
    foreign, _ = actor(app, "foreign")
    ids = [seed(app, user, short_code=f"own{i}").id for i in range(5)]
    seed(app, foreign, short_code="foreign")
    client = app.test_client()
    first = client.get("/api/v1/urls?size=2", headers=headers)
    assert first.status_code == 200
    body = first.json
    assert [item["id"] for item in body["items"]] == ids[:2]
    assert body["pagination"] == {
        "offset": 0,
        "size": 2,
        "has_more": True,
        "next_offset": 2,
        "next_before_id": None,
    }
    second = client.get("/api/v1/urls?size=2&offset=2", headers=headers).json
    assert [item["id"] for item in second["items"]] == ids[2:4]
    last = client.get("/api/v1/urls?size=2&offset=4", headers=headers).json
    assert [item["id"] for item in last["items"]] == ids[4:]
    assert last["pagination"]["has_more"] is False and last["pagination"]["next_offset"] is None
    page = client.get("/api/v1/urls?page=2&per_page=2", headers=headers).json
    assert page == second
    cursor = client.get(f"/api/v1/urls?before_id={ids[-1] + 1}&size=2", headers=headers).json
    assert [item["id"] for item in cursor["items"]] == ids[-2:][::-1]
    assert cursor["pagination"]["next_before_id"] == ids[-2]
    assert "sample" in client.get("/urls?size=2", headers=headers).json
    for query in ("size=no", "size=201", "offset=-1", "size=2&size=3", "page=2&offset=1"):
        assert client.get(f"/api/v1/urls?{query}", headers=headers).status_code == 400


def test_ownership_404_and_cross_user_confusion(app):
    owner, owner_headers = actor(app, "owner")
    attacker, attacker_headers = actor(app, "attacker")
    row = seed(app, owner, short_code="secret1")
    client = app.test_client()
    assert client.get(f"/urls/{row.id}", headers=attacker_headers).status_code == 404
    assert client.get(f"/api/v1/urls/{row.id}", headers=attacker_headers).status_code == 404
    assert (
        client.put(
            f"/urls/{row.id}", headers=attacker_headers, json={"title": "hijack"}
        ).status_code
        == 404
    )
    assert client.delete(f"/urls/{row.id}", headers=attacker_headers).status_code == 404
    assert client.get(f"/users/{owner.id}", headers=attacker_headers).status_code == 404
    assert (
        client.put(
            f"/users/{owner.id}", headers=attacker_headers, json={"username": "h"}
        ).status_code
        == 404
    )
    assert client.delete(f"/users/{owner.id}", headers=attacker_headers).status_code == 404
    assert client.get(
        f"/api/v1/urls/{row.id}/status?x=1", headers=attacker_headers
    ).status_code in (404, 503)
    assert client.get(f"/urls/{row.id}", headers=owner_headers).status_code == 200
    assert client.get(f"/users/{owner.id}", headers=owner_headers).status_code == 200
    # Unknown id: identical 404, no existence oracle for foreign rows.
    assert client.get("/urls/999999", headers=attacker_headers).status_code == 404


def test_admin_and_scope(app, monkeypatch):
    admin, admin_headers = actor(app, "admin")
    other, other_headers = actor(app, "other")
    seed(app, other, short_code="other1")
    app.config["ADMIN_USER_IDS"] = str(admin.id)
    assert client_admin(app, admin_headers, "/users").status_code == 200
    body = client_admin(app, admin_headers, "/users").json
    assert [u["username"] for u in body["sample"]] == ["admin", "other"]
    assert client_admin(app, other_headers, "/users").json["sample"][0]["username"] == "other"
    assert client_admin(app, other_headers, "/users/1").status_code == 404
    assert client_admin(app, other_headers, "/logs").status_code == 403
    assert client_admin(app, admin_headers, "/logs").status_code == 200


def client_admin(app, headers, path):
    return app.test_client().get(path, headers=headers)


def test_idempotency_namespace(app, monkeypatch):

    captured = []

    def fake_publish(payload):
        captured.append(payload)
        with app.app_context():
            code = f"code{len(captured)}"
            row = Url.create(
                user=payload["user_id"],
                short_code=code,
                original_url=payload["original_url"],
                title=payload["title"],
                request_id=payload["request_id"],
            )
        return {
            "id": row.id,
            "short_code": code,
            "original_url": payload["original_url"],
            "title": payload["title"],
            "user_id": payload["user_id"],
            "is_active": True,
        }

    monkeypatch.setattr("app.routes.urls.publish_url_create", fake_publish)
    owner, headers = actor(app, "owner")
    attacker, attacker_headers = actor(app, "attacker")
    client = app.test_client()
    first = client.post(
        "/urls",
        headers={**headers, "Idempotency-Key": "retry-me"},
        json={"original_url": "https://example.com", "title": "t"},
    )
    assert first.status_code == 201
    replay = client.post(
        "/urls",
        headers={**headers, "Idempotency-Key": "retry-me"},
        json={"original_url": "https://example.com", "title": "t"},
    )
    assert replay.status_code == 200 and replay.json["id"] == 1
    stolen = client.post(
        "/urls",
        headers={**attacker_headers, "Idempotency-Key": "retry-me"},
        json={"original_url": "https://example.com", "title": "t"},
    )
    assert stolen.status_code in (201, 202)  # attacker creates their own; never the owner's row
    stolen2 = client.post(
        "/urls",
        headers={**attacker_headers, "Idempotency-Key": "retry-me"},
        json={"original_url": "https://example.com", "title": "t"},
    )
    assert stolen2.status_code == 200 and stolen2.json.get("id") != 1
    with app.app_context():
        rows = list(Url.select().where(Url.request_id.is_null(False)))
        assert rows, "URLs must persist a namespaced request_id"
        assert all(r.request_id.startswith(f"u{r.user_id}:") for r in rows)
        assert len({r.request_id for r in rows}) == len(rows)


def test_status_not_leaked_without_ownership(app, monkeypatch):
    owner, owner_headers = actor(app, "owner")
    attacker, attacker_headers = actor(app, "attacker")

    class FakeRedis:
        data = {}

        def get(self, key):
            return FakeRedis.data.get(key)

    fake = FakeRedis()
    fake.data["url-pending:legacy-req"] = json.dumps(
        {
            "status": "ready",
            "id": 7,
            "short_code": "zz",
            "original_url": "https://secret.test",
            "title": "s",
        }
    )
    monkeypatch.setattr("app.cache.get_l2", lambda: fake)
    client = app.test_client()
    legacy = client.get("/urls/legacy-req/status", headers=attacker_headers)
    assert legacy.status_code == 404  # no owned row -> unowned Redis payload is unreachable
    with app.app_context():
        from app.models.url import Url as UrlModel

        UrlModel.create(
            user=owner,
            short_code="zz2",
            original_url="https://x.test",
            title="x",
            request_id="legacy-req",
        )
    assert client.get("/urls/legacy-req/status", headers=owner_headers).status_code == 200
    assert client.get("/urls/legacy-req/status", headers=attacker_headers).status_code == 404


def test_rate_limit_headers_and_429(app, monkeypatch):
    owner, headers = actor(app, "lim")
    client = app.test_client()
    script = Mock(return_value=[0, 0.0])

    class FakeLimitRedis:
        def register_script(self, _):
            return script

    monkeypatch.setattr("app.utils.ratelimit.get_l2", lambda: FakeLimitRedis())
    monkeypatch.setattr("app.utils.ratelimit._script_obj", None)
    app.config["RATELIMIT_IN_TESTS"] = True
    resp = client.get("/urls?size=1", headers=headers)
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] and int(resp.headers["X-RateLimit-Remaining"]) == 0
    script.return_value = [1, 5.0]
    resp = client.get("/urls?size=1", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["X-RateLimit-Remaining"] == "5"
    assert resp.headers["X-RateLimit-Limit"] == "300"


def test_create_event_ownership(app):
    owner, owner_headers = actor(app, "evowner")
    attacker, attacker_headers = actor(app, "evattacker")
    row = seed(app, owner, short_code="ev1")
    client = app.test_client()
    ok = client.post(
        "/events", headers=owner_headers, json={"url_id": row.id, "event_type": "click"}
    )
    assert ok.status_code == 201
    assert ok.json["user_id"]["id"] == owner.id
    assert (
        client.post(
            "/events", headers=attacker_headers, json={"url_id": row.id, "event_type": "click"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/events",
            headers=owner_headers,
            json={"url_id": row.id, "user_id": attacker.id, "event_type": "click"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/events",
            headers=owner_headers,
            json={"url_id": row.id, "event_type": "click", "unknown": 1},
        ).status_code
        == 400
    )


def test_openapi_spec_and_docs(app):
    client = app.test_client()
    spec = client.get("/openapi.json").json
    assert spec["openapi"].startswith("3.1")
    assert "/api/v1/urls" in spec["paths"] and "/api/v1/urls/{url_id}" in spec["paths"]
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert "responses" in op and "security" in op
    assert client.get("/docs").status_code == 200
    public_post = spec["paths"]["/api/v1/users"]["post"]
    assert public_post["security"] == []
    list_op = spec["paths"]["/api/v1/urls"]["get"]
    assert any(p["name"] == "before_id" for p in list_op["parameters"])
    assert "Page" in spec["components"]["schemas"]
