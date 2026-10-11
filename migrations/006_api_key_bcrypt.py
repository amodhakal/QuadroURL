"""Add the bcrypt-era API key columns: ``key_prefix`` and ``key_lookup``.

API keys were previously stored as a bare ``sha256(raw)`` in ``key_hash``. That
digest is fast and unsalted, so an offline attacker with a copy of the table can
brute-force a 43-character ``token_urlsafe(32)`` key far more cheaply than bcrypt
allows. Digests are now bcrypt hashes, which cannot be used to *locate* a row
because the salt differs per key — hence the deterministic ``key_lookup`` HMAC
column that keeps verification a single indexed query.

Backfill
--------
``key_lookup`` and ``key_prefix`` are both derived from the *raw* key, which is
never stored, so existing rows cannot be backfilled and are left NULL. There is
no value derivable from the old ``sha256`` column that would reconstruct either
one. Consequently **every API key issued before this migration stops
authenticating** and its row becomes unreachable by lookup.

This is intentional and is the accepted cost of the change: those keys are
already compromised by the weakness being fixed, so they must be re-issued. Rows
left NULL are inert — they cannot be matched by ``key_lookup`` and so can never
authenticate — but they should still be deleted operationally, e.g.
``DELETE FROM "apikey" WHERE "key_lookup" IS NULL``.

Once rotated, ``key_hash`` no longer holds a plain SHA-256 digest, so the
remaining 64-character values are still stored (the column stays unique and
populated, keeping the model and indexes unchanged) but are never compared
against a presented key.
"""

from peewee import CharField, PostgresqlDatabase, SqliteDatabase
from playhouse.migrate import PostgresqlMigrator, SqliteMigrator, migrate


def _migrator(database):
    if isinstance(database, SqliteDatabase):
        return SqliteMigrator(database)
    if isinstance(database, PostgresqlDatabase):
        return PostgresqlMigrator(database)
    raise RuntimeError(f"Unsupported database for 006_api_key_bcrypt: {type(database).__name__}")


def _column_names(database, table):
    return {column.name for column in database.get_columns(table)}


def _add_column(database, migrator, table, name, field):
    if not database.table_exists(table) or name in _column_names(database, table):
        return False
    migrate(migrator.add_column(table, name, field))
    return True


def _create_lookup_index(database):
    """Enforce uniqueness of ``key_lookup`` on upgraded databases.

    ``ADD COLUMN ... UNIQUE`` is not portable (SQLite rejects it), so uniqueness
    is added as a standalone index instead. A freshly created table gets the same
    guarantee from the model's field-level ``unique=True``; both paths enforce it.
    """
    database.execute_sql(
        'CREATE UNIQUE INDEX IF NOT EXISTS "apikey_key_lookup" ON "apikey" ("key_lookup")'
    )


def upgrade(database, models):
    migrator = _migrator(database)
    _add_column(
        database,
        migrator,
        "apikey",
        "key_prefix",
        CharField(max_length=8, null=True, default=None),
    )
    _add_column(
        database,
        migrator,
        "apikey",
        "key_lookup",
        CharField(max_length=64, null=True, default=None),
    )
    if database.table_exists("apikey"):
        _create_lookup_index(database)