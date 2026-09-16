from datetime import datetime

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
    is_active = BooleanField()
    # Client idempotency key for POST /urls (#113). Nullable so pre-existing
    # rows stay valid; unique so each key maps to exactly one row. Postgres
    # treats NULLs as distinct, so old rows never clash.
    request_id = CharField(null=True, unique=True)
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        indexes = (
            (("user",), False),
            (("is_active",), False),
            (("original_url",), False),
        )

    def save(self, *args, **kwargs):
        self.updated_at = datetime.now()
        return super().save(*args, **kwargs)
