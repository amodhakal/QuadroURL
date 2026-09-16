"""Runner contract tests for #156 — hermetic SQLite, no services."""

import pytest
from peewee import SqliteDatabase


@pytest.fixture(autouse=True)
def clean_tables():
    yield  # no integration DB needed


def _models(database):
    from shared.schema import create_models

    return create_models(database)


def test_upgrade_creates_schema_and_records_history():
    from migrations_runner import applied, discover, upgrade

    database = SqliteDatabase(":memory:")
    models = _models(database)
    with database:
        assert upgrade(database, models) == ["001_canonical_schema", "003_delivery"]
        assert applied(database) == {"001_canonical_schema", "003_delivery"}
        assert discover()[0][0] == "001_canonical_schema"

        # Idempotent: re-running applies nothing new.
        assert upgrade(database, models) == []
        assert applied(database) == {"001_canonical_schema", "003_delivery"}

        # The schema is usable end to end.
        user = models.User.create(username="mig", email="mig@example.com")
        url = models.Url.create(
            user_id=user.id,
            short_code="mig001",
            original_url="https://example.com",
            title="Migration smoke",
        )
        assert url.is_active is True


def test_upgrade_applies_pending_migrations_in_order():
    from migrations_runner import applied, upgrade

    database = SqliteDatabase(":memory:")
    models = _models(database)
    with database:
        upgrade(database, models)
        assert applied(database) == {"001_canonical_schema", "003_delivery"}
        # A fresh database runs everything from scratch.
        fresh = SqliteDatabase(":memory:")
        fresh_models = _models(fresh)
        with fresh:
            assert upgrade(fresh, fresh_models) == ["001_canonical_schema", "003_delivery"]
