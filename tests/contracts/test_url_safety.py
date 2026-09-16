"""Defensive policy contracts: no destination DNS lookup or HTTP requests."""

import io
from unittest.mock import MagicMock

import pytest
import requests

from app.utils.url_safety import reputation_status, require_destination_safe, validate_destination


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/path?q=hello#section",
        "http://example.com:8080",
        "https://8.8.8.8/",
        "https://[2606:4700:4700::1111]/",
        "https://bücher.de/",
    ],
)
def test_public_destinations_are_accepted_without_network(value, monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: pytest.fail("DNS is forbidden"))
    assert validate_destination(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "//example.com",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fe80::1]/",
        "http://service.internal/",
        "http://foo.local/",
        "http://user:pass@example.com/",
        "http://example.com:99999/",
        "http://example.com:0/",
        "http://example.com\\path",
        "https://example.com/\nheader",
        "http://127.1/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://127.0.0.1./",
        "http://[fe80::1%25eth0]/",
        "http://example.com%00/",
        "http://host/",
    ],
)
def test_unsafe_destinations_are_rejected(value):
    with pytest.raises(ValueError):
        validate_destination(value)


def provider(app, monkeypatch, body=b"{}", status=200):
    app.config.update(SAFE_BROWSING_PROVIDER="google", SAFE_BROWSING_API_KEY="test-key")
    session = MagicMock()
    session.__enter__.return_value = session
    response = MagicMock()
    response.status_code = status
    response.raw = io.BytesIO(body)
    raw_read = response.raw.read
    response.raw = MagicMock()
    response.raw.read.side_effect = lambda n, **kw: raw_read(n)
    session.post.return_value.__enter__.return_value = response
    monkeypatch.setattr("app.utils.url_safety.requests.Session", lambda: session)
    return session


def test_disabled_is_not_live_verification(app, monkeypatch):
    app.config["SAFE_BROWSING_PROVIDER"] = "disabled"
    monkeypatch.setattr(
        "app.utils.url_safety.requests.Session", lambda: pytest.fail("not opted in")
    )
    with app.app_context():
        assert reputation_status("https://example.com") == "disabled"


@pytest.mark.parametrize(
    "body,status,expected",
    [
        (b"{}", 200, "no_match"),
        (b'{"matches":[{}]}', 200, "unsafe"),
        (b"bad json", 200, "unavailable"),
        (b"{}", 302, "unavailable"),
        (b'{"matches":{}}', 200, "unavailable"),
        (b"[]", 200, "unavailable"),
        (b"x" * 65537, 200, "unavailable"),
    ],
)
def test_provider_fixed_endpoint_bounded_request(app, monkeypatch, body, status, expected):
    session = provider(app, monkeypatch, body, status)
    with app.app_context():
        assert reputation_status("https://example.com") == expected
    args, kwargs = session.post.call_args
    assert args == ("https://safebrowsing.googleapis.com/v4/threatMatches:find",)
    assert kwargs["timeout"] == (2, 3)
    assert kwargs["allow_redirects"] is False
    assert session.trust_env is False


def test_timeout_failure_policy_and_unsafe_blocking(app, monkeypatch):
    from werkzeug.exceptions import Forbidden, ServiceUnavailable

    session = provider(app, monkeypatch)
    session.post.side_effect = requests.Timeout()
    with app.app_context():
        with pytest.raises(ServiceUnavailable):
            require_destination_safe("https://example.com")
        app.config["SAFE_BROWSING_FAILURE_POLICY"] = "open"
        assert require_destination_safe("https://example.com") == "unavailable"
        session.post.side_effect = None
        provider(app, monkeypatch, b'{"matches":[{}]}')
        with pytest.raises(Forbidden):
            require_destination_safe("https://example.com")


def test_creation_cannot_enqueue_unsafe_links(app, monkeypatch):
    from app.models import User
    from app.utils.auth import issue_api_key

    user = User.create(username="safety", email="safety@example.com")
    _, key = issue_api_key(user.id)
    monkeypatch.setattr(
        "app.routes.urls.publish_url_create", lambda *a: pytest.fail("must not enqueue")
    )
    headers = {"Authorization": f"Bearer {key}"}
    client = app.test_client()
    for prefix in ("", "/api/v1"):
        response = client.post(
            prefix + "/urls",
            headers=headers,
            json={"title": "unsafe", "original_url": "http://localhost/"},
        )
        assert response.status_code == 400
    provider(app, monkeypatch, b'{"matches":[{}]}')
    response = client.post(
        "/api/v1/urls",
        headers=headers,
        json={"title": "flagged", "original_url": "https://example.com"},
    )
    assert response.status_code == 403
