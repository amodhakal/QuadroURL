"""Unit tests for isolated functions — Input A → Output B, with mocks where needed."""

import json
from unittest.mock import patch, MagicMock

from app.routes.urls import generate_short_code, format_url


# ---------------------------------------------------------------------------
# generate_short_code(): Input = length, Output = alphanumeric string
# ---------------------------------------------------------------------------

def test_generate_short_code_default_length():
    """Input: no args. Output: 6-char alphanumeric string."""
    code = generate_short_code()
    assert len(code) == 6
    assert code.isalnum()


def test_generate_short_code_custom_length():
    """Input: length=10. Output: 10-char string."""
    code = generate_short_code(length=10)
    assert len(code) == 10
    assert code.isalnum()


def test_generate_short_code_produces_different_codes():
    """Two calls should (almost certainly) produce different codes."""
    codes = {generate_short_code() for _ in range(20)}
    assert len(codes) > 1


# ---------------------------------------------------------------------------
# format_url(): Input = Url model instance, Output = dict with user_id key
# ---------------------------------------------------------------------------

@patch("app.routes.urls.model_to_dict")
def test_format_url_renames_user_to_user_id(mock_m2d):
    """Input: url object. Output: dict where 'user' key is renamed to 'user_id'."""
    mock_m2d.return_value = {
        "id": 1,
        "user": 42,
        "short_code": "abc123",
        "original_url": "https://example.com",
        "title": "Example",
        "is_active": True,
    }
    result = format_url(MagicMock())
    assert "user_id" in result
    assert "user" not in result
    assert result["user_id"] == 42


@patch("app.routes.urls.model_to_dict")
def test_format_url_preserves_other_fields(mock_m2d):
    """All non-user fields should be passed through unchanged."""
    mock_m2d.return_value = {
        "id": 5,
        "user": 1,
        "short_code": "xyz789",
        "original_url": "https://test.com",
        "title": "Test",
        "is_active": False,
    }
    result = format_url(MagicMock())
    assert result["id"] == 5
    assert result["short_code"] == "xyz789"
    assert result["original_url"] == "https://test.com"
    assert result["title"] == "Test"
    assert result["is_active"] is False


# ---------------------------------------------------------------------------
# create_event(): Input = params, Output = Event.create called with JSON details
# ---------------------------------------------------------------------------

@patch("app.utils.events.Event")
def test_create_event_calls_event_create(mock_event):
    """Input: url_id, user_id, type, details dict. Output: Event.create called."""
    from app.utils.events import create_event

    create_event(1, 2, "created", {"short_code": "abc123"})

    mock_event.create.assert_called_once()
    call_kwargs = mock_event.create.call_args[1]
    assert call_kwargs["url_id"] == 1
    assert call_kwargs["user_id"] == 2
    assert call_kwargs["event_type"] == "created"


@patch("app.utils.events.Event")
def test_create_event_serializes_details_to_json(mock_event):
    """Input: details dict. Output: details stored as JSON string."""
    from app.utils.events import create_event

    details = {"short_code": "abc123", "original_url": "https://example.com"}
    create_event(1, 2, "created", details)

    stored_details = mock_event.create.call_args[1]["details"]
    assert isinstance(stored_details, str)
    assert json.loads(stored_details) == details


@patch("app.utils.events.Event")
def test_create_event_handles_empty_details(mock_event):
    """Input: empty dict. Output: valid JSON string '{}'."""
    from app.utils.events import create_event

    create_event(1, 2, "created", {})

    stored_details = mock_event.create.call_args[1]["details"]
    assert json.loads(stored_details) == {}


# ---------------------------------------------------------------------------
# _do_create_event(): retry then log after IntegrityError exhaustion
# ---------------------------------------------------------------------------

@patch("app.utils.events.Event")
@patch("app.utils.events.time.sleep")
def test_do_create_event_logs_after_integrity_retries(mock_sleep, mock_event):
    """Input: a persistently colliding Event.create. Output: it is retried
    _MAX_RETRIES times and then logs an error instead of raising."""
    from peewee import IntegrityError

    from app.utils.events import _MAX_RETRIES, _do_create_event

    mock_event.create.side_effect = IntegrityError("fk violation")

    _do_create_event(1, 2, "created", {"short_code": "abc123"})

    assert mock_event.create.call_count == _MAX_RETRIES
