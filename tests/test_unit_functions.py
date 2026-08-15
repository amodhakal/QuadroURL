"""Unit tests for isolated functions — Input A → Output B, with mocks where needed."""

from unittest.mock import MagicMock, patch

from app.routes.urls import format_url, generate_short_code


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
# create_event(): Input = params, Output = publish_event called with payload
# ---------------------------------------------------------------------------

@patch("app.utils.events.publish_event")
def test_create_event_calls_publish_event(mock_publish):
    """Input: url_id, user_id, type, details dict. Output: publish_event called."""
    from app.utils.events import create_event

    create_event(1, 2, "created", {"short_code": "abc123"})

    mock_publish.assert_called_once()
    payload = mock_publish.call_args[0][0]
    assert payload["url_id"] == 1
    assert payload["user_id"] == 2
    assert payload["event_type"] == "created"


@patch("app.utils.events.publish_event")
def test_create_event_passes_details_dict_through(mock_publish):
    """Input: details dict. Output: details passed through unchanged."""
    from app.utils.events import create_event

    details = {"short_code": "abc123", "original_url": "https://example.com"}
    create_event(1, 2, "created", details)

    payload = mock_publish.call_args[0][0]
    assert payload["details"] == details


@patch("app.utils.events.publish_event")
def test_create_event_handles_empty_details(mock_publish):
    """Input: empty dict. Output: empty details published."""
    from app.utils.events import create_event

    create_event(1, 2, "created", {})

    payload = mock_publish.call_args[0][0]
    assert payload["details"] == {}
