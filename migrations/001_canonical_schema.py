"""001: create the canonical tables from shared.schema (issue #156).

Base schema for fresh installs. Idempotent for existing databases:
``create_tables(safe=True)`` skips tables that already exist.
"""


def upgrade(database, models):
    database.create_tables(
        [models.User, models.Url, models.Event, models.RequestLog, models.ApiKey],
        safe=True,
    )
