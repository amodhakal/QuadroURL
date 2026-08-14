import json
import logging
import random
import string
import time

import redis
from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DateTimeField,
    IntegerField,
    Model,
    TextField,
)
from playhouse.pool import PooledPostgresqlDatabase

import config

logger = logging.getLogger("consumer.url_create")


class Url(Model):
    id = AutoField()
    user_id = IntegerField()
    short_code = CharField(unique=True)
    original_url = CharField()
    title = CharField()
    is_active = BooleanField(default=True)
    created_at = DateTimeField()
    updated_at = DateTimeField()

    class Meta:
        database = None
        table_name = "url"


def generate_short_code(length=6):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def handle_url_create(data, db, redis_client):
    request_id = data.get("request_id")
    user_id = data.get("user_id")
    original_url = data.get("original_url")
    title = data.get("title")
    pending_key = f"url-pending:{request_id}"

    if not all([request_id, user_id, original_url, title]):
        logger.warning(f"Invalid url-create message: {data}")
        if request_id:
            redis_client.setex(
                pending_key,
                3600,
                json.dumps({"status": "error", "error": "Missing required fields"}),
            )
        return

    try:
        db.connect(reuse_if_open=True)

        url = None
        for attempt in range(5):
            short_code = generate_short_code()
            try:
                url = Url.create(
                    user_id=user_id,
                    short_code=short_code,
                    original_url=original_url,
                    title=title,
                    is_active=True,
                )
                break
            except Exception:
                continue

        if url is None:
            logger.error(f"Failed to generate short code for request_id={request_id}")
            redis_client.setex(
                pending_key,
                3600,
                json.dumps({"status": "error", "error": "Failed to generate unique short code"}),
            )
            return

        url_data = {
            "id": url.id,
            "user_id": url.user_id,
            "short_code": url.short_code,
            "original_url": url.original_url,
            "title": url.title,
            "is_active": url.is_active,
            "created_at": url.created_at.isoformat() if url.created_at else None,
            "updated_at": url.updated_at.isoformat() if url.updated_at else None,
        }

        redis_client.setex(
            pending_key,
            3600,
            json.dumps({
                "status": "ready",
                "id": url.id,
                "short_code": url.short_code,
                "original_url": url.original_url,
                "title": url.title,
            }),
        )

        logger.info(
            f"URL created: id={url.id} short_code={url.short_code} "
            f"request_id={request_id}"
        )

    except Exception:
        logger.exception(f"Failed to create URL for request_id={request_id}")
        redis_client.setex(
            pending_key,
            3600,
            json.dumps({"status": "error", "error": "Internal error creating URL"}),
        )
    finally:
        if not db.is_closed():
            db.close()
