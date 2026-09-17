"""Explicit, bounded delivery worker: python -m shared.delivery_worker [--once]."""

import argparse
import http.client
import ipaddress
import os
import socket
import ssl
import time
from urllib.parse import urlsplit

TIMEOUT = 5
LEASE_SECONDS = 60
MAX_ATTEMPTS = 8


def allowed_topics():
    return {
        os.getenv("KAFKA_TOPIC_REQUEST_LOGS", "request-logs"),
        os.getenv("KAFKA_TOPIC_URL_EVENTS", "url-events"),
        os.getenv("KAFKA_TOPIC_URL_CREATES", "url-creates"),
    }


def validate_destination(destination):
    """Exact operator-controlled URLs only; no wildcard or user-managed allowlist."""
    allowed = {v.strip() for v in os.getenv("WEBHOOK_ALLOWED_URLS", "").split(",") if v.strip()}
    if not isinstance(destination, str) or destination not in allowed:
        raise ValueError("Destination is not operator-approved")
    parsed = urlsplit(destination)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Destination must be an HTTPS URL on port 443")
    return parsed


def post_webhook(destination, payload, delivery_id):
    parsed = validate_destination(destination)
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    ips = [item[4][0] for item in addresses]
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("Webhook DNS must resolve only to public addresses")

    class PinnedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            # Connect to the validated IP, not a second DNS lookup (rebinding).
            raw = socket.create_connection((ips[0], 443), timeout=TIMEOUT)
            try:
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            except BaseException:
                raw.close()
                raise

    connection = PinnedHTTPSConnection(
        parsed.hostname, timeout=TIMEOUT, context=ssl.create_default_context()
    )
    try:
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection.request(
            "POST",
            path,
            body=payload.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": delivery_id,
                "X-Quadro-Delivery-ID": delivery_id,
            },
        )
        status = connection.getresponse().status
        # Never follow redirects or read an unbounded response body.
        if not 200 <= status < 300:
            raise RuntimeError(f"HTTP {status}")
    finally:
        connection.close()


def publish_replay(producer, replay):
    letter = replay.dead_letter
    if letter.topic not in allowed_topics():
        raise ValueError("Source topic is not replayable")
    result = []
    producer.produce(
        letter.topic,
        value=replay.payload,
        key=letter.message_key,
        headers={"quadro-replay-id": replay.id.encode("ascii")},
        on_delivery=lambda error, message: result.append(error),
    )
    producer.flush(TIMEOUT)
    if not result or result[0] is not None:
        raise RuntimeError("Kafka delivery not acknowledged")


def process_due(model, send, now=None, limit=50):
    """CAS leases allow restart recovery and multiple workers without long DB locks."""
    now = time.time() if now is None else now
    rows = list(
        model.select()
        .where((model.state == "pending") & (model.available_at <= now))
        .order_by(model.available_at)
        .limit(limit)
    )
    processed = 0
    for row in rows:
        attempt = row.attempts + 1
        lease = now + LEASE_SECONDS
        claimed = (
            model.update(attempts=attempt, available_at=lease)
            .where(
                (model.id == row.id)
                & (model.state == "pending")
                & (model.attempts == row.attempts)
                & (model.available_at <= now)
            )
            .execute()
        )
        if not claimed:
            continue
        values = dict(state="sent", last_error="")
        try:
            send(row)
        except Exception as exc:
            values = dict(
                state="failed" if attempt >= MAX_ATTEMPTS else "pending",
                available_at=now + min(3600, 2**attempt),
                last_error=type(exc).__name__,
            )
        model.update(**values).where(
            (model.id == row.id) & (model.attempts == attempt) & (model.available_at == lease)
        ).execute()
        processed += 1
    return processed


def run_once(models, producer):
    process_due(models.Replay, lambda row: publish_replay(producer, row))

    def send(row):
        if not row.subscription.enabled:
            return
        post_webhook(row.subscription.destination, row.payload, row.id)

    process_due(models.Delivery, send)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    from confluent_kafka import Producer
    from peewee import PostgresqlDatabase
    from shared.schema import create_models
    from shared.delivery_models import create_delivery_models

    database = PostgresqlDatabase(
        os.getenv("DATABASE_NAME", "hackathon_db"),
        host=os.getenv("DATABASE_HOST", "postgres"),
        port=int(os.getenv("DATABASE_PORT", "5432")),
        user=os.getenv("DATABASE_USER", "postgres"),
        password=os.getenv("DATABASE_PASSWORD", "postgres"),
        connect_timeout=5,
    )
    models = create_delivery_models(database, create_models(database))
    producer = Producer(
        {
            "bootstrap.servers": os.getenv("KAFKA_BROKER", "kafka:9092"),
            "enable.idempotence": True,
            "message.timeout.ms": 5000,
        }
    )
    try:
        while True:
            with database.connection_context():
                run_once(models, producer)
            if args.once:
                break
            time.sleep(1)
    finally:
        producer.flush(TIMEOUT)
        if not database.is_closed():
            database.close()


if __name__ == "__main__":
    main()
