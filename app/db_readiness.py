"""Role-aware PostgreSQL probe: connectivity alone accepts a read-only standby."""


def check_writable_primary(database):
    """Raise when this connection cannot serve as the writer.

    Check both recovery and session read-only state without modifying application
    data. This is a role check, not proof of every table's grants or free disk.
    The transaction-local timeout cannot leak into a pooled application session.
    """
    # Guard BEFORE the transaction block: once _atomic.__enter__ has begun the
    # transaction, in_transaction() is definitionally True, so checking inside
    # would reject every call. The guard also keeps a caller's outer
    # transaction from swallowing the SET LOCAL below.
    if database.in_transaction():
        raise RuntimeError(
            "check_writable_primary requires a fresh connection without an open "
            "transaction, so SET LOCAL and the probe share one session"
        )
    with database.atomic():
        database.execute_sql("SET LOCAL statement_timeout = '2000ms'")
        row = database.execute_sql(
            "SELECT pg_is_in_recovery(), current_setting('transaction_read_only')"
        ).fetchone()
        if row is None or row[0] is not False or row[1] != "off":
            raise RuntimeError("PostgreSQL is not a writable primary")
