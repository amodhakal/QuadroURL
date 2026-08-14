from datetime import datetime

from peewee import (
    AutoField,
    CharField,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    TextField,
)

from app.database import BaseModel
from app.models.url import Url


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
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        indexes = (
            (("created_at",), False),
            (("short_code",), False),
            (("status_code",), False),
        )
