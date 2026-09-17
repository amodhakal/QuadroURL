"""Standard pagination envelope across core v1 lists (#193).

Users, URLs, events, and logs share ``app.utils.pagination.envelope``:
v1 returns ``{items, pagination}`` with offset/size/has_more/next_offset/
next_before_id, while legacy routes keep their original shapes. Exports and
delivery admin lists are intentionally separate bounded shapes (documented in
#185/#184) and are not part of this envelope.
"""

import json

from app.models import Event, Url, User
from app.utils.auth import issue_api_key


def _actor(app, name):
    with app.app_context():
        user = User.create(username=name, email=f"{name}@example.com")
        _, key = issue_api_key(user.id)
    return user, {"Authorization": f"Bearer {key}"}


def test_core_v1_lists_share_the_envelope(app):
    owner, headers = _actor(app, "envelope")
    admin, admin_headers = _actor(app, "envadmin")
    _actor(app, "envthird")
    app.config["ADMIN_USER_IDS"] = str(admin.id)
    with app.app_context():
        for index in range(3):
            url = Url.create(
                user=owner,
                short_code=f"env{index}",
                original_url="https://example.com",
                title="t",
            )
            Event.create(
                url=url,
                user=owner,
                event_type="click",
                details=json.dumps({}),
            )
    client = app.test_client()

    for path, auth in (
        ("/api/v1/urls?size=2", headers),
        ("/api/v1/events?size=2", headers),
        ("/api/v1/users?size=2", admin_headers),
    ):
        response = client.get(path, headers=auth)
        assert response.status_code == 200
        body = response.json
        assert set(body) == {"items", "pagination"}
        assert set(body["pagination"]) == {
            "offset",
            "size",
            "has_more",
            "next_offset",
            "next_before_id",
        }
        assert len(body["items"]) == 2
        assert body["pagination"]["has_more"] is True
        assert body["pagination"]["next_offset"] == 2

    last = client.get("/api/v1/urls?size=2&offset=2", headers=headers).json
    assert len(last["items"]) >= 1
    assert last["pagination"]["has_more"] is False
    assert last["pagination"]["next_offset"] is None

    logs = client.get("/api/v1/logs?size=2", headers=admin_headers)
    assert logs.status_code == 200
    assert set(logs.json) == {"items", "pagination"}
    assert set(logs.json["pagination"]) == {
        "offset",
        "size",
        "has_more",
        "next_offset",
        "next_before_id",
    }


def test_legacy_shapes_are_preserved(app):
    owner, headers = _actor(app, "legacy")
    client = app.test_client()
    assert set(client.get("/urls?size=2", headers=headers).json) == {"kind", "sample"}
    assert "sample" in client.get("/users?size=2", headers=headers).json
