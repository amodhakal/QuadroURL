"""Make the Event foreign keys cascade on delete.

``shared/schema.py`` now declares ``Event.url`` and ``Event.user`` with
``on_delete="CASCADE"``. Fresh databases get that straight from
``create_models``; existing ones were built with the implicit NO ACTION, so
deleting a user or a URL raised ``IntegrityError`` (#303).

PostgreSQL can retarget a foreign key in place, so the constraint is dropped
and re-added. SQLite cannot alter a foreign key without rebuilding the whole
table, and its ``PRAGMA foreign_keys`` cannot be toggled inside the runner's
transaction — so SQLite installations are left alone: their schema comes from
``create_models`` on every test/dev bootstrap and is already CASCADE.
"""

from peewee import PostgresqlDatabase


_FK_QUERY = """
SELECT tc.constraint_name, kcu.column_name, rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
 AND tc.constraint_schema = kcu.constraint_schema
JOIN information_schema.referential_constraints rc
  ON tc.constraint_name = rc.constraint_name
 AND tc.constraint_schema = rc.constraint_schema
WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = %s
"""

# Event FK -> the table it points at. Mirrors shared/schema.py.
_EVENT_FKS = {"url_id": "url", "user_id": "user"}


def _postgres_foreign_keys(database, table):
    cursor = database.execute_sql(_FK_QUERY, (table,))
    try:
        return [(row[0], row[1], row[2]) for row in cursor.fetchall()]
    finally:
        cursor.close()


def _cascade_event_fks(database, models):
    table = models.Event._meta.table_name
    existing = _postgres_foreign_keys(database, table)
    for constraint, column, delete_rule in existing:
        if column not in _EVENT_FKS or delete_rule.upper() == "CASCADE":
            continue
        database.execute_sql(f'ALTER TABLE "{table}" DROP CONSTRAINT "{constraint}"')
        database.execute_sql(
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{constraint}" '
            f'FOREIGN KEY ("{column}") REFERENCES "{_EVENT_FKS[column]}" ("id") '
            "ON DELETE CASCADE ON UPDATE CASCADE"
        )


def upgrade(database, models):
    if not database.table_exists(models.Event._meta.table_name):
        return
    if isinstance(database, PostgresqlDatabase):
        _cascade_event_fks(database, models)
