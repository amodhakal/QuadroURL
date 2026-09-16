"""Export ownership, bounded pagination and safe CSV serialization."""

import csv
import io

import pytest

from app.models import Url, User
from app.utils.auth import issue_api_key


@pytest.fixture()
def export_client(app):
    from app.routes.exports import exports_bp

    app.register_blueprint(exports_bp, url_prefix="/api/v1")
    owner = User.create(username="owner", email="owner@example.com")
    other = User.create(username="other", email="other@example.com")
    _, key = issue_api_key(owner.id)
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {key}"
    return client, owner, other


def test_export_scopes_and_continues(export_client):
    client, owner, other = export_client
    for index, user in enumerate([other, owner, other, owner]):
        Url.create(
            user=user,
            short_code=f"code{index}",
            original_url="https://example.com",
            title="Example",
        )
    response = client.get("/api/v1/exports/urls?limit=1")
    assert response.status_code == 200
    assert len(response.json["data"]) == 1
    assert response.json["data"][0]["user_id"] == owner.id
    cursor = response.json["pagination"]["next_after_id"]
    following = client.get(f"/api/v1/exports/urls?limit=1&after_id={cursor}").json
    assert following["data"][0]["user_id"] == owner.id
    assert following["data"][0]["id"] > cursor
    assert following["pagination"]["has_more"] is False
    assert response.headers["Cache-Control"] == "no-store"


def test_csv_quotes_and_defuses_formulas(export_client):
    client, owner, _ = export_client
    Url.create(
        user=owner,
        short_code="formula",
        original_url="https://example.com",
        title='=SUM(1,2)\n"quoted"',
    )
    response = client.get("/api/v1/exports/urls?format=csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert response.status_code == 200
    assert len(rows) == 1
    assert rows[0]["title"] == '\'=SUM(1,2)\n"quoted"'
    assert "request_id" not in rows[0]
    assert response.headers["Content-Disposition"] == 'attachment; filename="urls.csv"'


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=1001", "limit=bad", "after_id=-1", "format=xml", "user_id=2"]
)
def test_export_rejects_invalid_parameters(export_client, query):
    client, _, _ = export_client
    assert client.get(f"/api/v1/exports/users?{query}").status_code == 400


def test_export_requires_credentials_and_hides_other_users(export_client):
    client, owner, _ = export_client
    assert client.get("/api/v1/exports/users", headers={"Authorization": ""}).status_code == 401
    rows = client.get("/api/v1/exports/users").json["data"]
    assert [row["id"] for row in rows] == [owner.id]
    assert client.get("/api/v1/exports/api_keys").status_code == 404
