"""Transactional inbox/outbox operations. No network IO or threads on import."""

import json
import time
from dataclasses import dataclass
from uuid import uuid4

from peewee import DataError, IntegrityError


@dataclass
class Poison:
    error: str


def source_key(msg):
    return f"{msg.topic()}:{msg.partition()}:{msg.offset()}"


def receipt_key(msg, models):
    # Only accept replay identities backed by our durable outbox, not arbitrary headers.
    for name, value in msg.headers() or []:
        if name == "quadro-replay-id" and value:
            try:
                replay_id = value.decode("ascii")
            except (UnicodeError, AttributeError):
                continue
            replay = models.Replay.get_or_none(models.Replay.id == replay_id)
            if (
                replay is not None
                and replay.dead_letter.topic == msg.topic()
                and replay.payload == msg.value()
            ):
                return f"replay:{replay.id}"
    return source_key(msg)


def quarantine(msg, error, models):
    """Caller commits this transaction before acknowledging any Kafka offset."""
    return models.DeadLetter.get_or_create(
        source=source_key(msg),
        defaults=dict(
            topic=msg.topic(),
            partition=msg.partition(),
            offset=msg.offset(),
            payload=msg.value(),
            message_key=msg.key(),
            error=str(error)[:2000],
            created_at=time.time(),
        ),
    )[0]


def decode_message(msg):
    data = json.loads(msg.value().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Message must be a JSON object")
    return data


def schedule_milestones(core, delivery, url_id):
    """Called within the click transaction, after locking its URL row."""
    count = (
        core.Event.select()
        .where((core.Event.url == url_id) & (core.Event.event_type == "click"))
        .count()
    )
    subscriptions = delivery.Subscription.select().where(
        (delivery.Subscription.url == url_id)
        & delivery.Subscription.enabled
        & (delivery.Subscription.milestone <= count)
    )
    for subscription in subscriptions:
        delivery.Delivery.get_or_create(
            subscription=subscription,
            defaults={
                "id": str(uuid4()),
                "payload": json.dumps(
                    {
                        "type": "click.milestone",
                        "url_id": url_id,
                        "milestone": subscription.milestone,
                    }
                ),
            },
        )


def persist_rows(buffer, database, core, delivery, model, on_write=None):
    """Isolate permanent row failures using savepoints; retry outages as a batch.

    The receipt prevents duplicates after DB commit / Kafka commit crashes.
    Unknown failures propagate, preventing the caller from committing offsets.
    """
    with database.atomic():
        for payload, msg in buffer:
            if isinstance(payload, Poison):
                quarantine(msg, payload.error, delivery)
                continue
            key = receipt_key(msg, delivery)
            if delivery.Receipt.get_or_none(delivery.Receipt.source == key):
                continue
            try:
                with database.atomic():
                    # Serialize click counting even when producers use different partitions.
                    if model == core.Event:
                        owner = core.Url.get_by_id(payload["url_id"])
                        if owner.user_id != payload["user_id"]:
                            raise ValueError("Event owner does not match URL owner")
                        core.Url.update(updated_at=core.Url.updated_at).where(
                            core.Url.id == owner.id
                        ).execute()
                    delivery.Receipt.create(source=key)
                    row = model.create(**payload)
                    if on_write:
                        on_write(row)
            except (IntegrityError, DataError, ValueError, TypeError, core.Url.DoesNotExist) as exc:
                # A racing receipt is success, not poison.
                if not delivery.Receipt.get_or_none(delivery.Receipt.source == key):
                    quarantine(msg, type(exc).__name__, delivery)
