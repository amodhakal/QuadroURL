"""Define tables once, with independent model classes for each service connection.

Importing this module never imports Flask or opens a database connection.
Foreign-key ID accessors accept consumer payloads such as ``url_id`` directly;
using integer-only duplicate models never bypassed constraints in PostgreSQL.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    TextField,
)


def utcnow():
    return datetime.now(timezone.utc)


def create_models(database):
    """Build the canonical schema bound to a service's pool or DatabaseProxy."""

    class BaseModel(Model):
        class Meta:
            pass

    BaseModel._meta.database = database

    class User(BaseModel):
        id = AutoField()
        username = CharField(unique=True)
        email = CharField(unique=True)
        created_at = DateTimeField(default=utcnow)

    class Url(BaseModel):
        id = AutoField()
        user = ForeignKeyField(User, backref="urls")
        short_code = CharField(unique=True)
        original_url = CharField()
        title = CharField()
        is_active = BooleanField(default=True)
        expires_at = DateTimeField(null=True, default=None)
        request_id = CharField(null=True, unique=True)
        created_at = DateTimeField(default=utcnow)
        updated_at = DateTimeField(default=utcnow)

        class Meta:
            indexes = ((("user",), False), (("is_active",), False), (("original_url",), False))

        def save(self, *args, **kwargs):
            self.updated_at = utcnow()
            return super().save(*args, **kwargs)

    class LinkMetadata(BaseModel):
        url = ForeignKeyField(Url, backref="link_metadata", primary_key=True, on_delete="CASCADE")
        # Canonical JSON array of normalized tags; bounded at the API boundary.
        tags = TextField(default="[]")
        folder = CharField(max_length=80, default="", index=True)
        password_hash = TextField(default="")
        updated_at = DateTimeField(default=utcnow)

    class Event(BaseModel):
        id = AutoField()
        url = ForeignKeyField(Url, backref="events")
        user = ForeignKeyField(User, backref="events")
        event_type = CharField()
        timestamp = DateTimeField(default=utcnow)
        details = TextField()

        class Meta:
            indexes = (
                (("url", "user"), False),
                (("user", "event_type"), False),
                (("user", "timestamp"), False),
            )

    class RequestLog(BaseModel):
        id = AutoField()
        url = ForeignKeyField(Url, backref="request_logs", null=True)
        user_agent = TextField(default="")
        client_ip = CharField(default="")
        method = CharField()
        path = CharField()
        status_code = IntegerField()
        latency_ms = FloatField()
        short_code = CharField(default="")
        created_at = DateTimeField(default=utcnow)

        class Meta:
            indexes = (
                (("created_at",), False),
                (("short_code",), False),
                (("status_code",), False),
            )

    class ApiKey(BaseModel):
        id = AutoField()
        user = ForeignKeyField(User, backref="api_keys")
        key_hash = CharField(unique=True)
        name = CharField(default="")
        created_at = DateTimeField(default=utcnow)

    return SimpleNamespace(
        BaseModel=BaseModel, User=User, Url=Url, Event=Event, RequestLog=RequestLog, ApiKey=ApiKey,
        LinkMetadata=LinkMetadata,
    )
