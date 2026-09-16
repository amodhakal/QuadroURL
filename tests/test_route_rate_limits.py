"""Request-level rate-limit wiring without integration services (#176)."""

from unittest.mock import MagicMock

import pytest
from flask import Flask

from app.routes.urls import urls_bp
from app.routes.users import users_bp
from app.routes.events import events_bp
import app.utils.ratelimit as limiter


@pytest.fixture(autouse=True)
def clean_tables():
    """Do not initialize the integration database for this isolated app."""
    yield


@pytest.mark.parametrize(
    "method,path",
    [
        ("put", "/urls/1"),
        ("delete", "/urls/1"),
        ("put", "/users/1"),
        ("delete", "/users/1"),
        ("get", "/urls"),
        ("get", "/urls/1"),
        ("get", "/users"),
        ("get", "/users/1"),
        ("get", "/events"),
        ("get", "/urls/pending-id/status"),
    ],
)
def test_exhausted_bucket_rejects_request_before_database(method, path, monkeypatch):
    app = Flask(__name__)
    # TESTING deliberately false: the existing decorator bypasses limits in tests.
    app.register_blueprint(urls_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(events_bp)
    script = MagicMock(return_value=[0, 0])
    monkeypatch.setenv("RATELIMIT_ENABLED", "true")
    monkeypatch.setattr(limiter, "_script_obj", script)
    monkeypatch.setattr(limiter, "get_l2", lambda: object())

    response = getattr(app.test_client(), method)(path)

    assert response.status_code == 429
    assert response.json["error"] == "Rate limit exceeded"
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["X-RateLimit-Remaining"] == "0"
    script.assert_called_once()
    assert "/1" not in script.call_args.kwargs["keys"][0]
