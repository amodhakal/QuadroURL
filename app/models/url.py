from datetime import datetime, timezone

from peewee import (
    AutoField,
    CharField,
    DateTimeField,
    ForeignKeyField,
    BooleanField,
)

from app.database import BaseModel
from app.models.user import User


class Url(BaseModel):
    id = AutoField()
    user = ForeignKeyField(User, backref="urls")
    short_code = CharField(unique=True)
    original_url = CharField()
    title = CharField()
    is_active = BooleanField(default=True)
    # Soft-expiry for short links (#192). NULL means "never expires"; a set
    # value is compared against now (UTC) on the redirect path, where expired
    # links resolve as missing (404), mirroring inactive URLs. Optional at
    # creation (#134 rejects past values); NULL clears it on update.
    expires_at = DateTimeField(null=True, default=None)
    # Client idempotency key for POST /urls (#113). Nullable so pre-existing
    # rows stay valid; unique so each key maps to exactly one row. Postgres
    # treats NULLs as distinct, so old rows never clash.
    request_id = CharField(null=True, unique=True)
    created_at = DateTimeField(default=lambda: datetime.now(timezone.utc))
    updated_at = DateTimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        indexes = (
            (("user",), False),
            (("is_active",), False),
            (("original_url",), False),
        )

    def save(self, *args, **kwargs):
        self.updated_at = datetime.now(timezone.utc)
        return super().save(*args, **kwargs)
