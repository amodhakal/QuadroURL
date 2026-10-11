"""Runner contract tests for #156 — hermetic SQLite, no services."""

from datetime import datetime

import pytest
from peewee import IntegrityError, SqliteDatabase


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
        assert upgrade(database, models) == [
            "001_canonical_schema",
            "002_link_metadata",
            "003_delivery",
            "004_embeddings",
            "005_security_data_foundation",
            "006_event_fk_cascade",
        ]
        assert applied(database) == {
            "001_canonical_schema",
            "002_link_metadata",
            "003_delivery",
            "004_embeddings",
            "005_security_data_foundation",
            "006_event_fk_cascade",
        }
        assert discover()[0][0] == "001_canonical_schema"

        # Idempotent: re-running applies nothing new.
        assert upgrade(database, models) == []
        assert applied(database) == {
            "001_canonical_schema",
            "002_link_metadata",
            "003_delivery",
            "004_embeddings",
            "005_security_data_foundation",
            "006_event_fk_cascade",
        }

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
        assert applied(database) == {
            "001_canonical_schema",
            "002_link_metadata",
            "003_delivery",
            "004_embeddings",
            "005_security_data_foundation",
            "006_event_fk_cascade",
        }
        # A fresh database runs everything from scratch.
        fresh = SqliteDatabase(":memory:")
        fresh_models = _models(fresh)
        with fresh:
            assert upgrade(fresh, fresh_models) == [
                "001_canonical_schema",
                "002_link_metadata",
                "003_delivery",
                "004_embeddings",
                "005_security_data_foundation",
                "006_event_fk_cascade",
            ]


def _create_legacy_tables(database):
    statements = (
        'CREATE TABLE "user" (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, '
        "email TEXT NOT NULL UNIQUE, created_at DATETIME NOT NULL)",
        'CREATE TABLE "url" (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, '
        "short_code TEXT NOT NULL UNIQUE, original_url TEXT NOT NULL, title TEXT NOT NULL, "
        "is_active BOOLEAN NOT NULL, expires_at DATETIME, request_id TEXT, "
        "created_at DATETIME, updated_at DATETIME NOT NULL)",
        'CREATE TABLE "urlembedding" (url_id INTEGER PRIMARY KEY, embedding TEXT, '
        "content_hash TEXT NOT NULL, model TEXT NOT NULL, updated_at DATETIME NOT NULL)",
        'CREATE TABLE "event" (id INTEGER PRIMARY KEY, url_id INTEGER NOT NULL, '
        "user_id INTEGER NOT NULL, event_type TEXT NOT NULL, timestamp DATETIME NOT NULL, "
        "details TEXT NOT NULL)",
        'CREATE TABLE "requestlog" (id INTEGER PRIMARY KEY, url_id INTEGER, '
        "user_agent TEXT NOT NULL, client_ip TEXT NOT NULL, method TEXT NOT NULL, "
        "path TEXT NOT NULL, status_code INTEGER NOT NULL, latency_ms REAL NOT NULL, "
        "short_code TEXT NOT NULL, created_at DATETIME NOT NULL)",
        'CREATE TABLE "apikey" (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, '
        "key_hash TEXT NOT NULL UNIQUE, name TEXT NOT NULL, created_at DATETIME NOT NULL)",
        'CREATE TABLE "linkmetadata" (url_id INTEGER PRIMARY KEY, tags TEXT NOT NULL, '
        "folder TEXT NOT NULL, password_hash TEXT NOT NULL, updated_at DATETIME NOT NULL)",
        'CREATE TABLE "receipt" (source TEXT PRIMARY KEY)',
        'CREATE TABLE "deadletter" (id INTEGER PRIMARY KEY, source TEXT NOT NULL UNIQUE, '
        "topic TEXT NOT NULL, partition INTEGER NOT NULL, offset BIGINT NOT NULL, "
        "payload BLOB, message_key BLOB, error TEXT NOT NULL, created_at REAL NOT NULL)",
        'CREATE TABLE "replay" (id TEXT PRIMARY KEY, dead_letter_id INTEGER NOT NULL UNIQUE, '
        "payload BLOB, state TEXT NOT NULL, attempts INTEGER NOT NULL, available_at REAL NOT NULL, "
        "last_error TEXT NOT NULL, requested_by INTEGER NOT NULL)",
        'CREATE TABLE "subscription" (id INTEGER PRIMARY KEY, url_id INTEGER NOT NULL, '
        "destination TEXT NOT NULL, milestone BIGINT NOT NULL, enabled BOOLEAN NOT NULL)",
        'CREATE TABLE "delivery" (id TEXT PRIMARY KEY, subscription_id INTEGER NOT NULL UNIQUE, '
        "payload TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL, "
        "available_at REAL NOT NULL, last_error TEXT NOT NULL)",
    )
    for statement in statements:
        database.execute_sql(statement)


def _mark_legacy_migrations(database):
    from migrations_runner import _history_for

    history = _history_for(database)
    database.create_tables([history])
    for name in (
        "001_canonical_schema",
        "002_link_metadata",
        "003_delivery",
        "004_embeddings",
    ):
        history.create(migration_id=name, applied_at="2026-01-01T00:00:00+00:00")


def test_upgrade_repairs_legacy_request_ids_and_backfills_admin_membership(monkeypatch):
    from migrations_runner import applied, upgrade

    database = SqliteDatabase(":memory:")
    models = _models(database)
    with database:
        _create_legacy_tables(database)
        _mark_legacy_migrations(database)
        admin_id = database.execute_sql(
            'INSERT INTO "user" (username, email, created_at) VALUES (?, ?, ?)',
            ("admin", "admin@example.com", datetime(2026, 1, 1)),
        ).lastrowid
        other_id = database.execute_sql(
            'INSERT INTO "user" (username, email, created_at) VALUES (?, ?, ?)',
            ("other", "other@example.com", datetime(2026, 1, 1)),
        ).lastrowid
        for short_code, request_id, created_at in (
            ("later", "legacy-request", datetime(2026, 1, 3)),
            ("earliest", "legacy-request", datetime(2026, 1, 1)),
            ("tie", "legacy-request", datetime(2026, 1, 1)),
            ("null-date", "legacy-request", None),
            ("null-one", None, datetime(2026, 1, 1)),
            ("null-two", None, datetime(2026, 1, 1)),
        ):
            database.execute_sql(
                'INSERT INTO "url" (user_id, short_code, original_url, title, is_active, '
                "expires_at, request_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    admin_id,
                    short_code,
                    "https://example.com",
                    "Legacy",
                    True,
                    None,
                    request_id,
                    created_at,
                    created_at or datetime(2026, 1, 4),
                ),
            )
        database.execute_sql('INSERT INTO "receipt" (source) VALUES (?)', ("receipt-1",))
        database.execute_sql(
            'INSERT INTO "deadletter" (source, topic, partition, offset, error, created_at) '
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("topic:0:1", "url-events", 0, 1, "bad", 1.0),
        )
        database.execute_sql(
            'INSERT INTO "replay" (id, dead_letter_id, state, attempts, available_at, '
            "last_error, requested_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("replay-1", 1, "pending", 0, 0, "", admin_id),
        )
        monkeypatch.setenv("ADMIN_USER_IDS", f"{admin_id},not-an-id")

        assert upgrade(database, models) == [
            "005_security_data_foundation",
            "006_event_fk_cascade",
        ]
        assert applied(database) == {
            "001_canonical_schema",
            "002_link_metadata",
            "003_delivery",
            "004_embeddings",
            "005_security_data_foundation",
            "006_event_fk_cascade",
        }
        assert models.User.get_by_id(admin_id).is_admin is True
        assert models.User.get_by_id(other_id).is_admin is False
        assert [
            (row.short_code, row.request_id) for row in models.Url.select().order_by(models.Url.id)
        ] == [
            ("later", None),
            ("earliest", "legacy-request"),
            ("tie", None),
            ("null-date", None),
            ("null-one", None),
            ("null-two", None),
        ]
        assert {"is_admin"} <= {column.name for column in database.get_columns("user")}
        assert {"request_fingerprint"} <= {column.name for column in database.get_columns("url")}
        assert {
            "is_trusted",
            "source",
        } <= {column.name for column in database.get_columns("event")}
        assert "request_id" in {column.name for column in database.get_columns("requestlog")}
        assert {
            "expires_at",
            "revoked_at",
            "last_used_at",
        } <= {column.name for column in database.get_columns("apikey")}
        assert {"created_at"} <= {column.name for column in database.get_columns("receipt")}
        assert {
            "created_at",
            "updated_at",
            "completed_at",
        } <= {column.name for column in database.get_columns("replay")}
        from shared.delivery_models import create_delivery_models

        delivery = create_delivery_models(database, models)
        assert delivery.Receipt.get_by_id("receipt-1").created_at is not None
        partial_indexes = [
            index
            for index in database.get_indexes("url")
            if index.unique and index.sql and "WHERE" in index.sql.upper()
        ]
        assert len(partial_indexes) == 1
        with pytest.raises(IntegrityError):
            models.Url.create(
                user_id=admin_id,
                short_code="duplicate-request",
                original_url="https://example.com/duplicate",
                title="Duplicate",
                request_id="legacy-request",
            )


def test_fresh_schema_defaults_and_metadata():
    from migrations_runner import upgrade
    from shared.delivery_models import create_delivery_models

    database = SqliteDatabase(":memory:")
    models = _models(database)
    with database:
        upgrade(database, models)
        user = models.User.create(username="defaults", email="defaults@example.com")
        assert user.is_admin is False
        url = models.Url.create(
            user_id=user.id,
            short_code="defaults",
            original_url="https://example.com",
            title="Defaults",
        )
        event = models.Event.create(
            url_id=url.id,
            user_id=user.id,
            event_type="click",
            details="{}",
        )
        assert event.is_trusted is False
        assert event.source == ""
        request_log = models.RequestLog.create(
            method="GET", path="/", status_code=200, latency_ms=1.0
        )
        assert request_log.request_id is None
        key = models.ApiKey.create(user_id=user.id, key_hash="key-hash")
        assert key.expires_at is None
        assert key.revoked_at is None
        assert key.last_used_at is None
        assert ("url", "event_type", "timestamp") in {
            fields for fields, _ in models.Event._meta.indexes
        }
        indexes = database.get_indexes("url")
        assert any(index.unique and index.sql and "WHERE" in index.sql.upper() for index in indexes)
        delivery = create_delivery_models(database, models)
        assert delivery.Receipt(source="receipt").created_at is not None
        assert (
            delivery.Replay(id="replay", dead_letter=1, requested_by=user.id).updated_at is not None
        )
        assert delivery.Delivery(id="delivery", subscription=1, payload="{}").completed_at is None
