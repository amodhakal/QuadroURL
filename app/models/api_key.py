from datetime import datetime, timezone

from peewee import AutoField, CharField, DateTimeField, ForeignKeyField

from app.database import BaseModel
from app.models.user import User


class ApiKey(BaseModel):
    """Bearer credential identifying a user (#99).

    Only the sha256 hex digest is stored; the raw key is shown once at
    issuance and is never recoverable afterwards. New table, so existing
    deployments pick it up via ``create_tables(safe=True)`` in ``init_db``
    with no manual migration.
    """

    id = AutoField()
    user = ForeignKeyField(User, backref="api_keys")
    key_hash = CharField(unique=True)
    name = CharField(default="")
    created_at = DateTimeField(default=lambda: datetime.now(timezone.utc))
