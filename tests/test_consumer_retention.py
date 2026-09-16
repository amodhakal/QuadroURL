"""Request-log retention purge contract tests (#167) — hermetic SQLite.

``consumer/retention.py`` is loaded under an aliased module name (same
importlib pattern as ``test_consumer.py``) so nothing leaks between modules.
The shared schema provides the real table DDL; no Kafka, Redis, or Postgres.
"""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from peewee import SqliteDatabase

from shared.schema import create_models

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_retention():
    spec = importlib.util.spec_from_file_location(
        "consumer_retention", str(REPO_ROOT / "consumer" / "retention.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["consumer_retention"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def clean_tables():
    """Isolation: no integration DB fixture is needed here."""
    yield


@pytest.fixture()
def purge_env(tmp_path):
    # File-backed (not ":memory:"): the purge legitimately closes the
    # connection it opens, and in-memory SQLite loses the schema on close.
    database = SqliteDatabase(str(tmp_path / "retention.db"))
    models = create_models(database)
    database.create_tables([models.RequestLog])
    yield database, models.RequestLog, _load_retention()
    if not database.is_closed():
        database.close()


def _insert_row(RequestLog, created_at):
    return RequestLog.create(
        method="GET", path="/abc123", status_code=200, latency_ms=1.0, created_at=created_at
    )


def test_purge_deletes_only_rows_older_than_retention(purge_env):
    database, RequestLog, retention = purge_env
    now = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
    old = _insert_row(RequestLog, now - timedelta(days=8))
    fresh = _insert_row(RequestLog, now - timedelta(hours=1))

    with database:
        deleted = retention.purge_request_logs_older_than(
            database, RequestLog, retention_seconds=7 * 24 * 3600, now=now
        )

    assert deleted == 1
    assert RequestLog.get_or_none(RequestLog.id == fresh.id) is not None
    assert RequestLog.get_or_none(RequestLog.id == old.id) is None


def test_purge_on_empty_table_returns_zero(purge_env):
    database, RequestLog, retention = purge_env
    with database:
        assert retention.purge_request_logs_older_than(database, RequestLog, 3600) == 0


def test_purge_treats_naive_timestamps_as_utc(purge_env):
    """Postgres strips tzinfo on round-trip; naive rows must still purge."""
    database, RequestLog, retention = purge_env
    now = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
    stale_naive = (now - timedelta(days=10)).replace(tzinfo=None)
    row = _insert_row(RequestLog, stale_naive)

    with database:
        deleted = retention.purge_request_logs_older_than(
            database, RequestLog, retention_seconds=7 * 24 * 3600, now=now
        )

    assert deleted == 1
    assert RequestLog.get_or_none(RequestLog.id == row.id) is None


def test_purge_reconnects_when_pool_was_closed(purge_env):
    database, RequestLog, retention = purge_env
    database.close()  # outside any transaction
    # A closed pool must be reconnected by the purge itself (and re-closed).
    assert retention.purge_request_logs_older_than(database, RequestLog, 3600) == 0
    assert database.is_closed()
