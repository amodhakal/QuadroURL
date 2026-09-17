"""Legacy validation helpers and schema-first route validation contracts.

The helpers retain their original messages and bool-vs-int behavior (#150).
Routes now use Pydantic schemas (#191): strict positive IDs, field-prefixed
errors, and pagination sizes up to 200 rather than the helpers' limit of 100.
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
    """The legacy helper checks truthiness without stripping whitespace."""
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
# Route-level regressions: Pydantic schema messages and constraints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,message",
    [
        ("page=0", "page: Input should be greater than or equal to 1"),
        ("per_page=0", "per_page: Input should be greater than or equal to 1"),
        ("per_page=201", "per_page: Input should be less than or equal to 200"),
    ],
)
def test_route_users_pagination_messages(client, query, message):
    r = client.get(f"/users?{query}")
    assert r.status_code == 400
    assert r.get_json()["error"] == message


@pytest.mark.parametrize("route", ["/urls", "/events"])
@pytest.mark.parametrize(
    "query,message",
    [
        ("offset=-1", "offset: Input should be greater than or equal to 0"),
        ("size=0", "size: Input should be greater than or equal to 1"),
        ("size=201", "size: Input should be less than or equal to 200"),
    ],
)
def test_route_urls_events_pagination_messages(client, route, query, message):
    r = client.get(f"{route}?{query}")
    assert r.status_code == 400
    assert r.get_json()["error"] == message


@pytest.mark.parametrize("size", [101, 200])
@pytest.mark.parametrize(
    "route,param", [("/users", "per_page"), ("/urls", "size"), ("/events", "size")]
)
def test_route_pagination_accepts_schema_limit(client, route, param, size):
    """Routes must not retain the legacy helpers' upper bound of 100."""
    r = client.get(f"{route}?{param}={size}")
    assert r.status_code == 200
    payload = r.get_json()
    items = payload if route == "/events" else payload["sample"]
    assert isinstance(items, list)
    assert len(items) <= size


def test_route_create_user_messages(client):
    r = client.post("/users", content_type="application/json")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Invalid JSON"
    r = client.post("/users", json={"username": "   ", "email": "a@b.com"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "username: String should have at least 1 character"
    r = client.post(
        "/users",
        json={"username": "extra", "email": "extra@test.com", "role": "admin"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "role: Extra inputs are not permitted"


def test_route_update_user_unknown_fields(owner_client, sample_user):
    r = owner_client.put(f"/users/{sample_user.id}", json={"username": "ok", "role": "x"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "role: Extra inputs are not permitted"


@pytest.mark.parametrize("user_id", [True, False, "1", 1.5])
def test_route_url_user_id_requires_strict_int(client, user_id):
    """Unlike require_int, the schema rejects bools before ownership/lookup."""
    r = client.post(
        "/urls",
        json={"user_id": user_id, "original_url": "https://x.com", "title": "T"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "user_id: Input should be a valid integer"


@pytest.mark.parametrize("user_id", [0, -1])
def test_route_url_user_id_requires_positive_int(client, user_id):
    r = client.post(
        "/urls",
        json={"user_id": user_id, "original_url": "https://x.com", "title": "T"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "user_id: Input should be greater than 0"


def test_route_update_url_messages(owner_client, sample_url):
    r = owner_client.put(f"/urls/{sample_url.id}", json={"title": "   "})
    assert r.status_code == 400
    assert r.get_json()["error"] == "title: String should have at least 1 character"
    r = owner_client.put(f"/urls/{sample_url.id}", json={"is_active": "yes"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "is_active: Input should be a valid boolean"
    r = owner_client.put(f"/urls/{sample_url.id}", json={"bogus": 1})
    assert r.status_code == 400
    assert r.get_json()["error"] == "bogus: Extra inputs are not permitted"


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"details": []}, "details: Input should be a valid dictionary"),
        ({"url_id": True}, "url_id: Input should be a valid integer"),
        ({"url_id": False}, "url_id: Input should be a valid integer"),
        ({"url_id": 1.5}, "url_id: Input should be a valid integer"),
        ({"url_id": 0}, "url_id: Input should be greater than 0"),
        ({"event_type": "   "}, "event_type: String should have at least 1 character"),
    ],
)
def test_route_create_event_messages(owner_client, sample_url, sample_user, overrides, message):
    """Invalid shapes fail schema validation, not URL lookup or ownership."""
    r = owner_client.post(
        "/events",
        json={
            "url_id": sample_url.id,
            "user_id": sample_user.id,
            "event_type": "click",
            **overrides,
        },
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == message
