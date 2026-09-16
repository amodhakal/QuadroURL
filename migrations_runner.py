"""Explicit forward-only migrations: python -m migrations_runner upgrade.

Migration DDL and history are committed together. PostgreSQL upgrades serialize
using a transaction-scoped advisory lock; SQLite uses an immediate transaction.
Application and consumer startup never execute migrations.
"""

import argparse
import importlib
import os
import pkgutil
from datetime import datetime, timezone

from peewee import AutoField, CharField, Model, PostgresqlDatabase, SqliteDatabase

MIGRATIONS_PACKAGE = "migrations"
HISTORY_TABLE = "_migration_history"


def _history_for(database):
    class History(Model):
        id = AutoField()
        migration_id = CharField(max_length=64, unique=True)
        applied_at = CharField(max_length=64)

        class Meta:
            table_name = HISTORY_TABLE

    History.bind(database)
    return History


def discover():
    package = importlib.import_module(MIGRATIONS_PACKAGE)
    found = []
    for info in pkgutil.iter_modules(package.__path__):
        prefix = info.name.split("_", 1)[0]
        if prefix.isdigit():
            found.append((int(prefix), info.name))
    found.sort()
    if len({number for number, _ in found}) != len(found):
        raise RuntimeError("Duplicate migration numbers")
    return [(name, f"{MIGRATIONS_PACKAGE}.{name}") for _, name in found]


def applied(database):
    if not database.table_exists(HISTORY_TABLE):
        return set()
    return {row.migration_id for row in _history_for(database).select()}


def upgrade(database, models):
    """Apply pending migrations atomically; return their IDs. Caller owns connection."""
    History = _history_for(database)
    transaction = (
        database.atomic("IMMEDIATE") if isinstance(database, SqliteDatabase) else database.atomic()
    )
    with transaction:
        if isinstance(database, PostgresqlDatabase):
            database.execute_sql("SELECT pg_advisory_xact_lock(%s)", (716842056,))
        database.create_tables([History], safe=True)
        done = applied(database)
        migrations = discover()
        unknown = done - {name for name, _ in migrations}
        if unknown:
            raise RuntimeError(f"Database contains unknown migrations: {sorted(unknown)}")
        applied_now = []
        for name, module_path in migrations:
            if name not in done:
                importlib.import_module(module_path).upgrade(database, models)
                History.create(migration_id=name, applied_at=datetime.now(timezone.utc).isoformat())
                applied_now.append(name)
    return applied_now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["upgrade", "status"])
    args = parser.parse_args()
    database = PostgresqlDatabase(
        os.environ.get("DATABASE_NAME", "hackathon_db"),
        host=os.environ.get("DATABASE_HOST", "localhost"),
        port=int(os.environ.get("DATABASE_PORT", "5432")),
        user=os.environ.get("DATABASE_USER", "postgres"),
        password=os.environ.get("DATABASE_PASSWORD", "postgres"),
        connect_timeout=5,
    )
    from shared.schema import create_models

    try:
        database.connect()
        if args.command == "upgrade":
            print("Applied:", upgrade(database, create_models(database)))
        else:
            done = applied(database)
            for name, _ in discover():
                print(f"{'applied' if name in done else 'pending'} {name}")
    finally:
        if not database.is_closed():
            database.close()


if __name__ == "__main__":
    main()
