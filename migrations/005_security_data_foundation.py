"""Add the security, provenance, lifecycle, and retention foundation."""

import os

from peewee import BooleanField, CharField, DateTimeField, PostgresqlDatabase, SqliteDatabase
from playhouse.migrate import PostgresqlMigrator, SqliteMigrator, migrate

from shared.delivery_models import create_delivery_models
from shared.schema import utcnow


def _migrator(database):
    if isinstance(database, SqliteDatabase):
        return SqliteMigrator(database)
    if isinstance(database, PostgresqlDatabase):
        return PostgresqlMigrator(database)
    raise RuntimeError(
        f"Unsupported database for 005_security_data_foundation: {type(database).__name__}"
    )


def _column_names(database, table):
    return {column.name for column in database.get_columns(table)}


def _add_column(database, migrator, table, name, field):
    if not database.table_exists(table) or name in _column_names(database, table):
        return False
    migrate(migrator.add_column(table, name, field))
    return True


def _ensure_core_tables(database, models):
    tables = [
        models.User,
        models.Url,
        models.UrlEmbedding,
        models.Event,
        models.RequestLog,
        models.ApiKey,
        models.LinkMetadata,
    ]
    missing = [model for model in tables if not database.table_exists(model._meta.table_name)]
    if missing:
        database.create_tables(missing, safe=True)


def _ensure_delivery_tables(database, models):
    delivery = create_delivery_models(database, models)
    missing = [
        model
        for model in vars(delivery).values()
        if not database.table_exists(model._meta.table_name)
    ]
    if missing:
        database.create_tables(missing, safe=True)
    return delivery


def _add_core_columns(database, migrator, models):
    _add_column(database, migrator, "user", "is_admin", BooleanField(default=False))
    _add_column(
        database,
        migrator,
        "url",
        "expires_at",
        DateTimeField(null=True, default=None),
    )
    _add_column(
        database,
        migrator,
        "url",
        "request_id",
        CharField(null=True, default=None),
    )
    _add_column(
        database,
        migrator,
        "url",
        "request_fingerprint",
        CharField(max_length=64, null=True, default=None),
    )
    _add_column(
        database,
        migrator,
        "event",
        "is_trusted",
        BooleanField(default=False),
    )
    _add_column(
        database,
        migrator,
        "event",
        "source",
        CharField(max_length=64, default=""),
    )
    _add_column(
        database,
        migrator,
        "requestlog",
        "request_id",
        CharField(max_length=64, null=True, default=None),
    )
    for name in ("expires_at", "revoked_at", "last_used_at"):
        _add_column(
            database,
            migrator,
            "apikey",
            name,
            DateTimeField(null=True, default=None),
        )


def _add_delivery_columns(database, migrator, delivery):
    _add_column(
        database,
        migrator,
        "receipt",
        "created_at",
        DateTimeField(default=utcnow),
    )
    for table in ("replay", "delivery"):
        _add_column(
            database,
            migrator,
            table,
            "created_at",
            DateTimeField(default=utcnow),
        )
        _add_column(
            database,
            migrator,
            table,
            "updated_at",
            DateTimeField(default=utcnow),
        )
        _add_column(
            database,
            migrator,
            table,
            "completed_at",
            DateTimeField(null=True, default=None),
        )


def _backfill_admin_membership(database, models):
    models.User.update(is_admin=False).where(models.User.is_admin.is_null(True)).execute()
    admin_ids = set()
    for value in os.environ.get("ADMIN_USER_IDS", "").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            admin_id = int(value)
        except ValueError:
            continue
        if 0 < admin_id <= 2**31 - 1:
            admin_ids.add(admin_id)
    if admin_ids:
        models.User.update(is_admin=True).where(models.User.id.in_(admin_ids)).execute()


def _backfill_delivery_timestamps(database):
    database.execute_sql(
        'UPDATE "receipt" SET "created_at" = CURRENT_TIMESTAMP WHERE "created_at" IS NULL'
    )
    for table in ("replay", "delivery"):
        database.execute_sql(
            f'UPDATE "{table}" SET "created_at" = CURRENT_TIMESTAMP, '
            f'"updated_at" = COALESCE("updated_at", "created_at") '
            f'WHERE "created_at" IS NULL OR "updated_at" IS NULL'
        )


def _repair_request_ids(database, models):
    if not database.table_exists("url") or "request_id" not in _column_names(database, "url"):
        return
    created_at_order = (
        'CASE WHEN "created_at" IS NULL THEN 1 ELSE 0 END, "created_at", "id"'
        if "created_at" in _column_names(database, "url")
        else '"id"'
    )
    cursor = database.execute_sql(
        'SELECT "id", "request_id" FROM "url" '
        'WHERE "request_id" IS NOT NULL '
        f'ORDER BY "request_id", {created_at_order}'
    )
    seen = set()
    duplicate_ids = []
    try:
        for row in cursor:
            request_id = row[1]
            if request_id in seen:
                duplicate_ids.append(row[0])
            else:
                seen.add(request_id)
    finally:
        cursor.close()
    for start in range(0, len(duplicate_ids), 500):
        ids = duplicate_ids[start : start + 500]
        models.Url.update(request_id=None).where(models.Url.id.in_(ids)).execute()


def _create_indexes(database, models, delivery):
    for model in (
        models.User,
        models.Url,
        models.UrlEmbedding,
        models.Event,
        models.RequestLog,
        models.ApiKey,
        delivery.Receipt,
        delivery.Replay,
        delivery.Delivery,
    ):
        model._schema.create_indexes(safe=True)
    database.execute_sql(
        'CREATE UNIQUE INDEX IF NOT EXISTS "url_request_id_non_null" '
        'ON "url" ("request_id") WHERE "request_id" IS NOT NULL'
    )


def upgrade(database, models):
    migrator = _migrator(database)
    _ensure_core_tables(database, models)
    delivery = _ensure_delivery_tables(database, models)
    _add_core_columns(database, migrator, models)
    _add_delivery_columns(database, migrator, delivery)
    _backfill_admin_membership(database, models)
    _backfill_delivery_timestamps(database)
    _repair_request_ids(database, models)
    _create_indexes(database, models, delivery)
