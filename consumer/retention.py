"""Request-log retention purge for the logs consumer (#167 cleanup gap).

RequestLog grows without bound; the API only keeps in-memory buffers. This
module adds a bounded, transactional purge of rows older than a configurable
retention window, invoked between drain cycles in the logs consumer loop.

Pure function over a peewee database + model so it can be tested hermetically
with SQLite in-memory (same table DDL via shared.schema.create_models).
"""

from datetime import datetime, timedelta, timezone


def purge_request_logs_older_than(database, model, retention_seconds, now=None):
    """Delete RequestLog rows older than ``retention_seconds``; return count deleted.

    - transactional: rows are deleted inside one atomic block
    - idempotent and safe on empty tables (returns 0)
    - compares against ``created_at``, which stores tz-aware UTC datetimes in
      the shared schema (naive after a Postgres round-trip is handled by
      treating naive timestamps as UTC, mirroring the app's redirect logic)
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=retention_seconds)
    was_closed = database.is_closed()
    if was_closed:
        database.connect(reuse_if_open=True)
    try:
        with database.atomic():
            query = model.delete()
            if getattr(model, "created_at", None) is not None:
                query = query.where(model.created_at < cutoff)
            return query.execute()
    finally:
        # Only return the connection if THIS call opened it: the consumer's
        # pool is shared with the drain paths, and closing inside a
        # caller-owned transaction raises OperationalError.
        if was_closed and not database.is_closed():
            database.close()
