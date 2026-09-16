"""Tests for app/utils/validation.py shared helpers (issue #150).

Pins helper behavior, the bool-vs-int contract (``bool`` is a subclass of
``int`` and the pre-existing ``isinstance(x, int)`` checks accept ``True``),
and route-level regressions proving messages/status codes are unchanged.
"""

import pytest
from werkzeug.exceptions import BadRequest

from app.utils.validation import (
    reject_unknown_fields,
    require_bool,
    require_dict,
    require_int,
    require_json,
    require_non_empty_str,
    require_str,
    validate_offset_params,
    validate_page_params,
)


def expect_400(fn, *args, **kwargs):
    with pytest.raises(BadRequest) as exc_info:
        fn(*args, **kwargs)
    return exc_info.value.description


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------


def test_validate_page_params_ok(app):
    with app.test_request_context("/"):
        assert validate_page_params(1, 20) == (1, 20)
        assert validate_page_params(3, 100) == (3, 100)


def test_validate_page_params_bad_page(app):
    with app.test_request_context("/"):
        assert expect_400(validate_page_params, 0, 20) == "page must be >= 1"
        assert expect_400(validate_page_params, None, 20) == "page must be >= 1"


def test_validate_page_params_bad_per_page(app):
    with app.test_request_context("/"):
        assert expect_400(validate_page_params, 1, 0) == "per_page must be between 1 and 100"
        assert expect_400(validate_page_params, 1, 101) == "per_page must be between 1 and 100"
        assert expect_400(validate_page_params, 1, None) == "per_page must be between 1 and 100"


def test_validate_offset_params_ok(app):
    with app.test_request_context("/"):
        assert validate_offset_params(0, 20) == (0, 20)
        assert validate_offset_params(5, 100) == (5, 100)


def test_validate_offset_params_bad(app):
    with app.test_request_context("/"):
        assert expect_400(validate_offset_params, -1, 20) == "offset must be >= 0"
        assert expect_400(validate_offset_params, None, 20) == "offset must be >= 0"
        assert expect_400(validate_offset_params, 0, 0) == "size must be between 1 and 100"
        assert expect_400(validate_offset_params, 0, 101) == "size must be between 1 and 100"
        assert expect_400(validate_offset_params, 0, None) == "size must be between 1 and 100"


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------


def test_require_non_empty_str(app):
    with app.test_request_context("/"):
        assert require_non_empty_str("alice", "username must be a non-empty string")
        for bad in ("", "   ", None, 123, 0, False, ["x"]):
            assert (
                expect_400(require_non_empty_str, bad, "username must be a non-empty string")
                == "username must be a non-empty string"
            )


def test_require_str_allows_whitespace_only(app):
    """urls create / events event_type use a truthiness check, no blank check."""
    with app.test_request_context("/"):
        assert require_str("   ", "title must be a string") == "   "
        for bad in ("", None, 123, False):
            assert (
                expect_400(require_str, bad, "title must be a string") == "title must be a string"
            )


# ---------------------------------------------------------------------------
# Int helper + bool-vs-int contract
# ---------------------------------------------------------------------------


def test_require_int_accepts_truthy_int(app):
    with app.test_request_context("/"):
        assert require_int(5, "user_id must be an integer") == 5


def test_require_int_accepts_true_preserves_isinstance_contract(app):
    """Regression: routes used ``isinstance(x, int)`` which accepts ``True``."""
    with app.test_request_context("/"):
        assert require_int(True, "user_id must be an integer") is True


def test_require_int_rejects_falsy_and_non_int(app):
    with app.test_request_context("/"):
        for bad in (0, False, None, "", "1", 1.5, [1]):
            assert (
                expect_400(require_int, bad, "user_id must be an integer")
                == "user_id must be an integer"
            )


def test_require_bool(app):
    with app.test_request_context("/"):
        assert require_bool(True, "is_active must be a boolean") is True
        assert require_bool(False, "is_active must be a boolean") is False
        for bad in (1, 0, "true", None, "false"):
            assert (
                expect_400(require_bool, bad, "is_active must be a boolean")
                == "is_active must be a boolean"
            )


def test_require_dict(app):
    with app.test_request_context("/"):
        assert require_dict({}, "details must be an object") == {}
        assert require_dict({"a": 1}, "details must be an object") == {"a": 1}
        for bad in ([], "x", None, 5, True):
            assert (
                expect_400(require_dict, bad, "details must be an object")
                == "details must be an object"
            )


def test_reject_unknown_fields(app):
    with app.test_request_context("/"):
        assert reject_unknown_fields({"username": "a", "email": "b"}, {"username", "email"}) == {
            "username": "a",
            "email": "b",
        }
        assert (
            expect_400(
                reject_unknown_fields,
                {"username": "a", "role": "admin", "age": 99},
                {"username", "email"},
            )
            == "Unknown fields: ['age', 'role']"
        )


def test_require_json(app):
    with app.test_request_context("/users", method="POST", json={"username": "a"}):
        assert require_json("Invalid JSON received for create_user") == {"username": "a"}
    with app.test_request_context(
        "/users", method="POST", data="", content_type="application/json"
    ):
        assert expect_400(require_json, "Invalid JSON received for create_user") == "Invalid JSON"
    with app.test_request_context(
        "/users", method="POST", data="{}", content_type="application/json"
    ):
        assert expect_400(require_json, "Invalid JSON received for create_user") == "Invalid JSON"


# ---------------------------------------------------------------------------
# Route-level regressions: messages/status unchanged after helper migration
# ---------------------------------------------------------------------------


def test_route_users_pagination_messages(client):
    r = client.get("/users?page=0")
    assert r.status_code == 400
    assert r.get_json()["error"] == "page must be >= 1"
    r = client.get("/users?per_page=101")
    assert r.status_code == 400
    assert r.get_json()["error"] == "per_page must be between 1 and 100"


def test_route_urls_events_pagination_messages(client):
    r = client.get("/urls?offset=-1")
    assert r.status_code == 400
    assert r.get_json()["error"] == "offset must be >= 0"
    r = client.get("/urls?size=101")
    assert r.status_code == 400
    assert r.get_json()["error"] == "size must be between 1 and 100"
    r = client.get("/events?offset=-1")
    assert r.status_code == 400
    assert r.get_json()["error"] == "offset must be >= 0"
    r = client.get("/events?size=0")
    assert r.status_code == 400
    assert r.get_json()["error"] == "size must be between 1 and 100"


def test_route_create_user_messages(client):
    r = client.post("/users", content_type="application/json")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Invalid JSON"
    r = client.post("/users", json={"username": "   ", "email": "a@b.com"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "username must be a non-empty string"
    r = client.post(
        "/users",
        json={"username": "extra", "email": "extra@test.com", "role": "admin"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "Unknown fields: ['role']"


def test_route_update_user_unknown_fields(client, sample_user):
    r = client.put(f"/users/{sample_user.id}", json={"username": "ok", "role": "x"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "Unknown fields: ['role']"


def test_route_url_bool_user_id_passes_int_check(client):
    """True is accepted by the int check, so it falls through to User lookup."""
    r = client.post(
        "/urls",
        json={"user_id": True, "original_url": "https://x.com", "title": "T"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "User not found"
    r = client.post(
        "/urls",
        json={"user_id": False, "original_url": "https://x.com", "title": "T"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "user_id must be an integer"


def test_route_update_url_messages(client, sample_url):
    r = client.put(f"/urls/{sample_url.id}", json={"title": "   "})
    assert r.status_code == 400
    assert r.get_json()["error"] == "title must be a non-empty string"
    r = client.put(f"/urls/{sample_url.id}", json={"is_active": "yes"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "is_active must be a boolean"
    r = client.put(f"/urls/{sample_url.id}", json={"bogus": 1})
    assert r.status_code == 400
    assert r.get_json()["error"] == "Unknown fields: ['bogus']"


def test_route_create_event_messages(client, sample_url, sample_user):
    r = client.post(
        "/events",
        json={
            "url_id": sample_url.id,
            "user_id": sample_user.id,
            "event_type": "click",
            "details": [],
        },
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "details must be an object"
    # True passes the int check -> falls through to URL lookup failure.
    r = client.post("/events", json={"url_id": True, "user_id": sample_user.id, "event_type": "c"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "URL not found"
    r = client.post("/events", json={"url_id": 1.5, "user_id": sample_user.id, "event_type": "c"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "url_id must be an integer"
