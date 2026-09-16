"""Focused unit tests for the writable-primary readiness probe (#187).

These exercise the probe's Python contract against fakes only; they do not
prove PostgreSQL server behavior for pg_is_in_recovery / transaction_read_only.
"""

import pytest

from app.db_readiness import check_writable_primary


class FakeTransaction:
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        self.database._in_transaction = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.database._in_transaction = False
        return False


class FakeDatabase:
    """Records statement timing; atomic() toggles in_transaction() around it."""

    def __init__(self, row):
        self.row = row
        self._in_transaction = False
        self.statements = []

    def in_transaction(self):
        return self._in_transaction

    def atomic(self):
        return FakeTransaction(self)

    def execute_sql(self, sql):
        assert self.in_transaction(), f"{sql!r} ran outside the atomic() block"
        self.statements.append(sql)
        return self

    def fetchone(self):
        return self.row


class RaisingDatabase(FakeDatabase):
    def __init__(self, error):
        super().__init__(row=None)
        self.error = error

    def execute_sql(self, sql):
        super().execute_sql(sql)
        raise self.error


def test_probe_passes_for_writable_primary():
    database = FakeDatabase(row=(False, "off"))
    check_writable_primary(database)
    assert len(database.statements) == 2
    assert "statement_timeout" in database.statements[0]
    assert "pg_is_in_recovery" in database.statements[1]


@pytest.mark.parametrize(
    "row",
    [
        (True, "off"),  # standby in recovery
        (False, "on"),  # primary, but session forced read-only
        (True, "on"),
        (None, "off"),  # no row returned
    ],
)
def test_probe_rejects_standby_or_readonly_states(row):
    with pytest.raises(RuntimeError, match="not a writable primary"):
        check_writable_primary(FakeDatabase(row=row))


def test_connection_error_propagates_as_not_ready():
    with pytest.raises(ConnectionError, match="connection refused"):
        check_writable_primary(RaisingDatabase(ConnectionError("connection refused")))


def test_timeout_guard_shares_the_probe_connection():
    class NeverReturns(FakeDatabase):
        def execute_sql(self, sql):
            super().execute_sql(sql)
            assert "statement_timeout" in self.statements[0], (
                "timeout must be set on the same connection before the probe"
            )
            raise TimeoutError("statement timeout should have fired server-side")

    with pytest.raises(TimeoutError):
        check_writable_primary(NeverReturns(row=(False, "off")))


def test_rejects_call_inside_existing_transaction():
    class AlreadyInTransaction(FakeDatabase):
        def __init__(self):
            super().__init__(row=(False, "off"))
            self._in_transaction = True

    with pytest.raises(RuntimeError, match="open transaction"):
        check_writable_primary(AlreadyInTransaction())
