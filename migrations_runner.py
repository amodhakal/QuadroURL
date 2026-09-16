"""Minimal forward-only migration runner (issue #156).

Replaces boot-time ``create_tables(safe=True)`` (which cannot add columns to
existing tables and records no history) with explicit, ordered, idempotent
migrations recorded in a ``_migration_history`` table.

Contract:
- migration modules live in ``migrations/`` named ``NNN_description.py``
- each exposes ``upgrade(database, models)``; forward-only, no downgrades
- ``upgrade()`` runs each pending migration in one transaction and records it
- ``upgrade()`` is safe to run repeatedly: applied ids are skipped
- no new runtime dependency; works on SQLite (tests) and Postgres (prod)
"""

import importlib
import pkgutil
from datetime import datetime, timezone

from peewee import AutoField, CharField, Model

MIGRATIONS_PACKAGE = "migrations"
HISTORY_TABLE = "_migration_history"


class _History(Model):
    id = AutoField()
    migration_id = CharField(max_length=64, unique=True)
    applied_at = CharField(max_length=64)

    class Meta:
        table_name = HISTORY_TABLE


def _history_for(database):
    _History._meta.database = database
    return _History


def discover():
    """Return ordered [(migration_id, module_path)] from the migrations package."""
    package = importlib.import_module(MIGRATIONS_PACKAGE)
    found = []
    for info in pkgutil.iter_modules(package.__path__):
        prefix = info.name.split("_", 1)[0]
        if info.name.startswith("_") or not prefix.isdigit():
            continue
        found.append((int(prefix), info.name))
    found.sort()
    return [(name, f"{MIGRATIONS_PACKAGE}.{name}") for _, name in found]


def applied(database):
    """Set of migration ids already recorded in history (empty if table absent)."""
    History = _history_for(database)
    if not database.table_exists(HISTORY_TABLE):
        return set()
    return {row.migration_id for row in History.select()}


def upgrade(database, models):
    """Apply pending migrations; returns the ids applied by this call."""
    History = _history_for(database)
    database.create_tables([History], safe=True)
    done = applied(database)
    applied_now = []
    for name, module_path in discover():
        if name in done:
            continue
        module = importlib.import_module(module_path)
        with database.atomic():
            module.upgrade(database, models)
            History.create(migration_id=name, applied_at=now_iso())
        applied_now.append(name)
    return applied_now


def now_iso():
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat()
