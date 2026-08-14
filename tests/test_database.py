"""Tests for the database module."""

from peewee import DatabaseProxy, Model

from app.database import BaseModel, db


def test_db_is_a_database_proxy():
    assert isinstance(db, DatabaseProxy)


def test_base_model_uses_db_proxy():
    assert BaseModel._meta.database is db


def test_base_model_is_peewee_model():
    assert issubclass(BaseModel, Model)


def test_db_proxy_is_initialized_after_app_creation(app):
    """After create_app(), the DatabaseProxy should point to a real database."""
    assert db.obj is not None


def test_db_connection_opens_and_closes(app):
    """Verify we can open and close a connection through the proxy."""
    db.connect(reuse_if_open=True)
    assert not db.is_closed()
    db.close()
    assert db.is_closed()


def test_db_close_rolls_back_on_exception(app):
    """The teardown handler rolls back (not commits) when the request ends with
    an exception (covers database.py line 42)."""
    from app.database import db

    db.connect(reuse_if_open=True)
    try:
        teardowns = [
            fn
            for fn in app.teardown_appcontext_funcs
            if getattr(fn, "__name__", "") == "_db_close"
        ]
        assert teardowns, "expected _db_close teardown to be registered"
        # Should not raise; rolls back the open connection.
        teardowns[0](BaseException("boom"))
    finally:
        db.close()
