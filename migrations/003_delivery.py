"""Durable poison quarantine, replay outbox, and milestone deliveries."""

from shared.delivery_models import create_delivery_models


def upgrade(database, models):
    delivery = create_delivery_models(database, models)
    database.create_tables(list(vars(delivery).values()), safe=True)
