import pytest
from pydantic import ValidationError

from app.utils.schemas import UrlCreate, UrlUpdate, UserCreate, UserUpdate, EventCreate


@pytest.mark.parametrize(
    "schema,body",
    [
        (UrlCreate, {"user_id": True, "original_url": "https://example.com", "title": "t"}),
        (UrlCreate, {"original_url": "javascript:alert(1)", "title": "t"}),
        (
            UrlCreate,
            {
                "original_url": "https://example.com",
                "title": "t",
                "expires_at": "2000-01-01T00:00:00Z",
            },
        ),
        (UrlUpdate, {"is_active": "false"}),
        (UrlUpdate, {"title": None}),
        (UrlUpdate, {"expires_at": "2035-01-01T00:00:00"}),
        (UrlUpdate, {"expires_at": 2000000000}),
        (UserCreate, {"username": "a" * 256, "email": "a@b.c"}),
        (UserCreate, {"username": "a", "email": "a@b.c", "is_admin": True}),
        (UserUpdate, {"email": None}),
        (EventCreate, {"url_id": 1, "event_type": "click", "details": []}),
    ],
)
def test_reject_invalid_bodies(schema, body):
    with pytest.raises(ValidationError):
        schema.model_validate(body)


def test_partial_updates_and_nullable_expiry():
    assert UserUpdate.model_validate({}).model_dump(exclude_unset=True) == {}
    assert UrlUpdate.model_validate({"expires_at": None}).model_dump(exclude_unset=True) == {
        "expires_at": None
    }
