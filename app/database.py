import os

from flask import request
from peewee import DatabaseProxy, Model
from playhouse.pool import PooledPostgresqlDatabase

db = DatabaseProxy()


class BaseModel(Model):
    class Meta:
        database = db


def init_db(app):
    database = PooledPostgresqlDatabase(
        os.environ.get("DATABASE_NAME", "hackathon_db"),
        host=os.environ.get("DATABASE_HOST", "localhost"),
        port=int(os.environ.get("DATABASE_PORT", 5432)),
        user=os.environ.get("DATABASE_USER", "postgres"),
        password=os.environ.get("DATABASE_PASSWORD", "postgres"),
        max_connections=int(os.environ.get("DB_MAX_CONNECTIONS", 20)),
        stale_timeout=300,
        connect_timeout=5,
    )
    db.initialize(database)

    from app.models import User, Url, Event, RequestLog

    db.create_tables([User, Url, Event, RequestLog], safe=True)

    @app.before_request
    def _db_connect():
        if request.path == "/health":
            return
        db.connect(reuse_if_open=True)
        db.begin()

    @app.teardown_appcontext
    def _db_close(exc):
        if not db.is_closed():
            if exc is None:
                db.commit()
            else:
                db.rollback()
