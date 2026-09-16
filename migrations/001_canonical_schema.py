"""Baseline/adopt the deployed schema without dropping existing rows.

Legacy installations may predate expiry/idempotency. Add those nullable fields
before building indexes. Future migrations must be separate numbered modules.
"""

from peewee import CharField, DateTimeField, SqliteDatabase
from playhouse.migrate import PostgresqlMigrator, SqliteMigrator, migrate


def upgrade(database, models):
    if database.table_exists("url"):
        columns = {column.name for column in database.get_columns("url")}
        migrator = (SqliteMigrator if isinstance(database, SqliteDatabase) else PostgresqlMigrator)(
            database
        )
        if "expires_at" not in columns:
            migrate(migrator.add_column("url", "expires_at", DateTimeField(null=True)))
        if "request_id" not in columns:
            migrate(migrator.add_column("url", "request_id", CharField(null=True)))
    database.create_tables(
        [models.User, models.Url, models.Event, models.RequestLog, models.ApiKey],
        safe=True,
    )
