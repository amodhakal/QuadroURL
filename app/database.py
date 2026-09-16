import os

from flask import request
from peewee import DatabaseProxy
from playhouse.pool import PooledPostgresqlDatabase
from shared.schema import create_models

db = DatabaseProxy()
models = create_models(db)
BaseModel = models.BaseModel


def init_db(app):
    database = PooledPostgresqlDatabase(
        os.environ.get("DATABASE_NAME", "hackathon_db"),
        host=os.environ.get("DATABASE_HOST", "localhost"),
        port=int(os.environ.get("DATABASE_PORT", 5432)),
        user=os.environ.get("DATABASE_USER", "postgres"),
        password=os.environ.get("DATABASE_PASSWORD", "postgres"),
        # NOTE (#157): per gunicorn *worker process* pool (gthread threads
        # share it). Budgeted against Postgres max_connections — see
        # docs/capacity.md scaling rule before changing this default.
        max_connections=int(os.environ.get("DB_MAX_CONNECTIONS", 20)),
        stale_timeout=300,
        connect_timeout=5,
    )
    db.initialize(database)

    from app.models import User, Url, Event, RequestLog, ApiKey

    db.create_tables([User, Url, Event, RequestLog, ApiKey], safe=True)

    @app.before_request
    def _db_connect():
        if request.path in ("/health", "/metrics", "/logs", "/dashboard", "/prometheus-metrics"):
            return
        db.connect(reuse_if_open=True)

    @app.teardown_appcontext
    def _db_close(exc):
        if not db.is_closed():
            db.close()
