"""Unit tests for model definitions — no database, no HTTP, just structure checks."""

from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DateTimeField,
    ForeignKeyField,
    Model,
    TextField,
)

from app.database import BaseModel
from app.models.user import User
from app.models.url import Url
from app.models.event import Event


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------


def test_user_is_base_model():
    assert issubclass(User, BaseModel)


def test_user_has_id_field():
    assert isinstance(User.id, AutoField)


def test_user_has_username_field():
    assert isinstance(User.username, CharField)


def test_user_username_is_unique():
    assert User.username.unique is True


def test_user_has_email_field():
    assert isinstance(User.email, CharField)


def test_user_email_is_unique():
    assert User.email.unique is True


def test_user_has_created_at_field():
    assert isinstance(User.created_at, DateTimeField)


def test_user_created_at_has_default():
    assert User.created_at.default is not None


# ---------------------------------------------------------------------------
# Url model
# ---------------------------------------------------------------------------


def test_url_is_base_model():
    assert issubclass(Url, BaseModel)


def test_url_has_user_foreign_key():
    assert isinstance(Url.user, ForeignKeyField)


def test_url_user_fk_points_to_user():
    assert Url.user.rel_model is User


def test_url_has_short_code_field():
    assert isinstance(Url.short_code, CharField)


def test_url_short_code_is_unique():
    assert Url.short_code.unique is True


def test_url_has_original_url_field():
    assert isinstance(Url.original_url, CharField)


def test_url_has_title_field():
    assert isinstance(Url.title, CharField)


def test_url_has_is_active_field():
    assert isinstance(Url.is_active, BooleanField)


def test_url_has_created_at_field():
    assert isinstance(Url.created_at, DateTimeField)


def test_url_has_updated_at_field():
    assert isinstance(Url.updated_at, DateTimeField)


def test_url_has_save_override():
    """Url.save should be overridden to update updated_at."""
    assert Url.save is not Model.save


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


def test_event_is_base_model():
    assert issubclass(Event, BaseModel)


def test_event_has_url_foreign_key():
    assert isinstance(Event.url, ForeignKeyField)


def test_event_url_fk_points_to_url():
    assert Event.url.rel_model is Url


def test_event_has_user_foreign_key():
    assert isinstance(Event.user, ForeignKeyField)


def test_event_user_fk_points_to_user():
    assert Event.user.rel_model is User


def test_event_has_event_type_field():
    assert isinstance(Event.event_type, CharField)


def test_event_has_timestamp_field():
    assert isinstance(Event.timestamp, DateTimeField)


def test_event_timestamp_has_default():
    assert Event.timestamp.default is not None


def test_event_has_details_field():
    assert isinstance(Event.details, TextField)
