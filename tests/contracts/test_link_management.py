"""Focused link-management contracts; SQLite only, no external services."""

import pytest

from app.models import Url, User
from app.utils.auth import issue_api_key


@pytest.fixture()
def links(app):
    from app.database import models
    from app.routes.links import links_bp
    from app.routes.qr import qr_bp

    database = Url._meta.database
    metadata_models = [models.LinkMetadata]
    with database.bind_ctx(metadata_models):
        database.create_tables(metadata_models)
        app.register_blueprint(links_bp)
        app.register_blueprint(links_bp, url_prefix="/api/v1", name="links_v1")
        app.register_blueprint(qr_bp)
        app.register_blueprint(qr_bp, url_prefix="/api/v1", name="qr_v1")
        app.config["PUBLIC_BASE_URL"] = "https://links.example.com"
        owner = User.create(username="owner", email="owner@example.com")
        stranger = User.create(username="stranger", email="stranger@example.com")
        _, key = issue_api_key(owner.id)
        first = Url.create(
            user=owner, short_code="first", original_url="https://example.com/a", title="First"
        )
        other = Url.create(
            user=stranger, short_code="other", original_url="https://example.com/b", title="Other"
        )
        yield app.test_client(), {"Authorization": f"Bearer {key}"}, first, other


def test_metadata_tags_folder_search_and_ownership(links):
    client, headers, first, other = links
    response = client.patch(
        f"/api/v1/links/{first.id}/metadata",
        headers=headers,
        json={"tags": ["Work", "work"], "folder": "Projects"},
    )
    assert response.status_code == 200
    assert response.json["tags"] == ["work"]
    response = client.get("/api/v1/links?tag=work&folder=Projects&q=First", headers=headers)
    assert [item["id"] for item in response.json["items"]] == [first.id]
    assert client.get("/api/v1/links?tag=wor", headers=headers).json["items"] == []
    assert (
        client.patch(
            f"/api/v1/links/{other.id}/metadata", headers=headers, json={"tags": ["work"]}
        ).status_code
        == 404
    )
    assert client.get("/api/v1/links").status_code == 401


def test_bulk_activation_is_atomic_and_scoped(links):
    client, headers, first, other = links
    endpoint = "/api/v1/links/bulk-activation"
    mixed = client.post(
        endpoint, headers=headers, json={"ids": [first.id, other.id], "is_active": False}
    )
    assert mixed.status_code == 404
    assert Url.get_by_id(first.id).is_active
    deactivation = client.post(
        endpoint, headers=headers, json={"ids": [first.id], "is_active": False}
    )
    assert deactivation.json["updated"] == 1
    assert client.get("/r/first").status_code == 404
    reactivation = client.post(
        endpoint, headers=headers, json={"ids": [first.id], "is_active": True}
    )
    assert reactivation.status_code == 200
    assert client.get("/r/first").status_code == 200
    bad = client.post(endpoint, headers=headers, json={"ids": [first.id], "is_active": "false"})
    assert bad.status_code == 400


@pytest.mark.parametrize(
    "path",
    [
        "/r/first",
        "/urls/first/redirect",
        "/q/first",
        "/api/v1/r/first",
        "/api/v1/urls/first/redirect",
        "/api/v1/q/first",
    ],
)
def test_password_protects_every_alias_and_revokes_immediately(links, path):
    client, headers, first, _ = links
    from app.database import models

    url = f"/api/v1/links/{first.id}/metadata"
    assert client.patch(url, headers=headers, json={"password": "long-password"}).status_code == 200
    stored = models.LinkMetadata.get_by_id(first.id)
    assert "long-password" not in stored.password_hash
    for method in (client.get, client.head):
        denied = method(path)
        assert denied.status_code == 401
        assert "no-store" in denied.headers["Cache-Control"]
        assert b"example.com/a" not in denied.data
    assert client.post(path, json={"password": "wrong-password"}).status_code == 401
    assert client.post(path, json={"password": "long-password"}).status_code in (200, 302)
    assert client.get(path, headers={"X-Link-Password": "long-password"}).status_code in (200, 302)
    assert client.get(path, headers={"Accept": "text/html"}).status_code == 401
    client.patch(url, headers=headers, json={"password": "replacement-password"})
    assert client.get(path, headers={"X-Link-Password": "long-password"}).status_code == 401
    client.patch(url, headers=headers, json={"password": None})
    assert client.get(path).status_code in (200, 302)


def test_redirect_ignores_stale_active_cache_and_blocks_unsafe_destination(links):
    from app.cache import set_url_by_short_code
    from app.routes.urls import format_url

    client, _, first, _ = links
    set_url_by_short_code("first", format_url(first))
    Url.update(is_active=False).where(Url.id == first.id).execute()
    assert client.get("/r/first").status_code == 404
    Url.update(is_active=True, original_url="http://127.0.0.1/private").where(
        Url.id == first.id
    ).execute()
    assert client.get("/r/first").status_code == 400


def test_qr_svg_options_destination_scan_and_stats(links, monkeypatch):
    import segno
    from app.models import Event

    client, headers, first, other = links
    encoded = []
    make = segno.make

    def capture(value, **kwargs):
        encoded.append(value)
        return make(value, **kwargs)

    monkeypatch.setattr("app.routes.qr.segno.make", capture)
    response = client.get(
        f"/api/v1/links/{first.id}/qr.svg?scale=4&error=H&dark=%23000055", headers=headers
    )
    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    assert b"<svg" in response.data and b"#005" in response.data
    assert encoded == ["https://links.example.com/q/first"]
    assert client.get(f"/links/{other.id}/qr.svg", headers=headers).status_code == 404
    assert client.get(f"/links/{first.id}/qr.svg?scale=999", headers=headers).status_code == 400
    assert (
        client.get(f"/links/{first.id}/qr.svg?dark=%23ffffff", headers=headers).status_code == 400
    )
    assert client.get(f"/links/{first.id}/qr.svg?border=0", headers=headers).status_code == 400
    recorded = []
    monkeypatch.setattr("app.utils.events.create_event", lambda *args: recorded.append(args))
    assert client.get("/q/first").status_code == 302
    assert len(recorded) == 1 and recorded[0][2] == "qr_scan"
    assert client.head("/q/first").status_code == 302
    assert client.get("/q/first", headers={"User-Agent": "Googlebot"}).status_code == 302
    assert len(recorded) == 1
    Event.create(url=first, user=first.user_id, event_type="qr_scan", details="{}")
    assert client.get(f"/links/{first.id}/qr-stats", headers=headers).json["recorded_scans"] == 1
    assert client.get(f"/links/{other.id}/qr-stats", headers=headers).status_code == 404
