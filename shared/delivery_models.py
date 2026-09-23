"""Durable delivery tables, independently bound for the API and workers."""

from types import SimpleNamespace

from peewee import (
    AutoField,
    BigIntegerField,
    BlobField,
    BooleanField,
    CharField,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    TextField,
)

from shared.schema import utcnow


def create_delivery_models(database, core):
    class Base(Model):
        class Meta:
            pass

    Base._meta.database = database

    class DeadLetter(Base):
        id = AutoField()
        source = CharField(unique=True)
        topic = CharField()
        partition = IntegerField()
        offset = BigIntegerField()
        payload = BlobField(null=True)
        message_key = BlobField(null=True)
        error = TextField()
        created_at = FloatField()

    class Receipt(Base):
        # Successful writes and their receipts commit in the same transaction.
        source = CharField(primary_key=True)
        created_at = DateTimeField(default=utcnow)

        class Meta:
            indexes = ((("created_at",), False),)

    class Replay(Base):
        id = CharField(primary_key=True)
        dead_letter = ForeignKeyField(DeadLetter, unique=True, backref="replays")
        payload = BlobField(null=True)
        state = CharField(default="pending", index=True)
        attempts = IntegerField(default=0)
        available_at = FloatField(default=0, index=True)
        last_error = TextField(default="")
        requested_by = IntegerField()
        created_at = DateTimeField(default=utcnow)
        updated_at = DateTimeField(default=utcnow)
        completed_at = DateTimeField(null=True, default=None)

        class Meta:
            indexes = ((("state", "created_at"), False),)

        def save(self, *args, **kwargs):
            self.updated_at = utcnow()
            return super().save(*args, **kwargs)

    class Subscription(Base):
        id = AutoField()
        url = ForeignKeyField(core.Url, on_delete="CASCADE", backref="webhooks")
        destination = TextField()
        milestone = BigIntegerField()
        enabled = BooleanField(default=True)

        class Meta:
            indexes = ((("url", "destination", "milestone"), True),)

    class Delivery(Base):
        id = CharField(primary_key=True)
        subscription = ForeignKeyField(Subscription, unique=True, on_delete="CASCADE")
        payload = TextField()
        state = CharField(default="pending", index=True)
        attempts = IntegerField(default=0)
        available_at = FloatField(default=0, index=True)
        last_error = TextField(default="")
        created_at = DateTimeField(default=utcnow)
        updated_at = DateTimeField(default=utcnow)
        completed_at = DateTimeField(null=True, default=None)

        class Meta:
            indexes = ((("state", "created_at"), False),)

        def save(self, *args, **kwargs):
            self.updated_at = utcnow()
            return super().save(*args, **kwargs)

    return SimpleNamespace(
        DeadLetter=DeadLetter,
        Receipt=Receipt,
        Replay=Replay,
        Subscription=Subscription,
        Delivery=Delivery,
    )
