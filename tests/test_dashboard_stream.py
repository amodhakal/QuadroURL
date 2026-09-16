"""Offline SSE auth, payload, and JavaScript checks (#182)."""

import json
import re
import subprocess

import pytest
from flask import Flask
from peewee import SqliteDatabase

from app.models import User, ApiKey
from app.utils.auth import issue_api_key
from app.routes.dashboard import dashboard_bp, DASHBOARD_HTML


@pytest.fixture(autouse=True)
def clean_tables():
    yield


@pytest.fixture()
def dashboard_app(monkeypatch):
    from app.utils import kafka_lag

    database = SqliteDatabase(":memory:")
    with database.bind_ctx([User, ApiKey]):
        database.create_tables([User, ApiKey])
        owner = User.create(username="operator", email="operator@example.com")
        _, token = issue_api_key(owner.id)
        instance = Flask(__name__)
        instance.config.update(TESTING=True, ADMIN_USER_IDS=str(owner.id))
        instance.register_blueprint(dashboard_bp)
        monkeypatch.setattr(
            kafka_lag, "snapshot", lambda: {"available": True, "groups": {"logs": 7}}
        )
        client = instance.test_client()
        client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        yield client
        database.close()


def test_stream_first_event_and_disconnect(dashboard_app):
    response = dashboard_app.get("/dashboard/stream", buffered=False)
    try:
        assert response.status_code == 200
        assert response.mimetype == "text/event-stream"
        first = next(iter(response.response)).decode()
        body = json.loads(first.removeprefix("data: ").strip())
        assert body["kafka_lag"]["groups"]["logs"] == 7
        assert "system" in body["metrics"]
        assert isinstance(body["logs"], list)
        assert response.headers["X-Accel-Buffering"] == "no"
    finally:
        response.close()


def test_stream_requires_admin(dashboard_app):
    assert dashboard_app.get("/dashboard/stream", headers={"Authorization": ""}).status_code == 401
    dashboard_app.application.config["ADMIN_USER_IDS"] = ""
    assert dashboard_app.get("/dashboard/stream").status_code == 403


def test_inline_javascript_and_escape_vectors(tmp_path):
    script = re.findall(r"<script>(.*?)</script>", DASHBOARD_HTML, re.S)[0]
    path = tmp_path / "dashboard.js"
    path.write_text(script)
    subprocess.run(["node", "--check", str(path)], check=True, capture_output=True)
    escape = script[script.index("function escapeHtml") : script.index("function fmtUptime")]
    check = escape + "\nconsole.log(escapeHtml('<img src=x onerror=alert(1)>'));"
    result = subprocess.run(["node", "-e", check], check=True, capture_output=True, text=True)
    assert "<img" not in result.stdout
    assert "&lt;img" in result.stdout
    assert "setInterval(poll" not in script
    assert "Authorization" in script
