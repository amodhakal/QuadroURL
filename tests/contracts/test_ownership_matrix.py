"""Authentication and ownership matrix for destructive endpoints (#175).

Bearer keys gate every mutation; foreign resources read as 404 (no existence
oracle); administrators bypass ownership for destructive operations while
non-administrators are rejected from admin surfaces with 403.
"""

from app.models import Url, User
from app.utils.auth import issue_api_key


def _actor(app, name):
    with app.app_context():
        user = User.create(username=name, email=f"{name}@example.com")
        _, key = issue_api_key(user.id)
    return user, {"Authorization": f"Bearer {key}"}


def _url(app, user, code):
    with app.app_context():
        return Url.create(user=user, short_code=code, original_url="https://example.com", title="t")


def test_destructive_ownership_matrix(app):
    owner, owner_headers = _actor(app, "destruct_owner")
    attacker, attacker_headers = _actor(app, "destruct_attacker")
    admin, admin_headers = _actor(app, "destruct_admin")
    app.config["ADMIN_USER_IDS"] = str(admin.id)
    row = _url(app, owner, "doomed1")
    foreign = _url(app, attacker, "doomed2")
    client = app.test_client()

    assert client.delete(f"/urls/{row.id}").status_code == 401
    assert client.get(f"/urls/{row.id}").status_code == 401
    assert client.get(f"/urls/{row.id}", headers=attacker_headers).status_code == 404
    assert client.delete(f"/urls/{row.id}", headers=attacker_headers).status_code == 404
    # Attacker's own row is untouched by the failed attempts above.
    assert client.get(f"/urls/{foreign.id}", headers=attacker_headers).status_code == 200
    # Owner deletes their own row.
    assert client.delete(f"/urls/{row.id}", headers=owner_headers).status_code == 200
    assert client.get(f"/urls/{row.id}", headers=owner_headers).status_code == 404
    # Administrator deletes a foreign row the attacker could not touch.
    assert client.delete(f"/urls/{foreign.id}", headers=admin_headers).status_code == 200


def test_admin_surfaces_reject_non_admins(app):
    _, other_headers = _actor(app, "destruct_other")
    admin, admin_headers = _actor(app, "destruct_admin2")
    app.config["ADMIN_USER_IDS"] = str(admin.id)
    client = app.test_client()
    assert client.get("/logs", headers=other_headers).status_code == 403
    assert client.get("/logs", headers=admin_headers).status_code == 200
    assert client.get("/logs").status_code == 401
