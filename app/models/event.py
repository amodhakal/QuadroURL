from datetime import datetime, timezone

from peewee import AutoField, CharField, DateTimeField, ForeignKeyField, TextField

from app.database import BaseModel
from app.models.url import Url
from app.models.user import User


class Event(BaseModel):
    id = AutoField()
    url = ForeignKeyField(Url, backref="events")
    user = ForeignKeyField(User, backref="events")
    event_type = CharField()
    timestamp = DateTimeField(default=lambda: datetime.now(timezone.utc))
    details = TextField()

    class Meta:
        indexes = (
            (("url", "user"), False),
            (("user", "event_type"), False),
            (("user", "timestamp"), False),
        )
