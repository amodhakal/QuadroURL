"""API contract tests with SQLite and in-memory caches; no service connections."""

import pytest
from flask import Flask, jsonify
from peewee import SqliteDatabase
from werkzeug.exceptions import HTTPException

from app.models import User, Url, Event, ApiKey, RequestLog


@pytest.fixture()
def app(monkeypatch):
    from app.routes.users import users_bp
    from app.routes.urls import urls_bp
    from app.routes.events import events_bp
    from app.routes.auth import auth_bp
    from app import cache

    database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
    models = [User, Url, Event, ApiKey, RequestLog]
    with database.bind_ctx(models):
        database.create_tables(models)
        instance = Flask(__name__)
        instance.config.update(TESTING=True)
        for bp in (users_bp, urls_bp, events_bp, auth_bp):
            instance.register_blueprint(bp)
            instance.register_blueprint(bp, url_prefix="/api/v1", name=f"{bp.name}_v1")

        from app.routes.openapi import docs_bp
        from app.routes.logs import logs_bp
        from app.routes.fail import fail_bp

        instance.register_blueprint(docs_bp)
        instance.register_blueprint(logs_bp)
        instance.register_blueprint(logs_bp, url_prefix="/api/v1", name="logs_v1")
        instance.register_blueprint(fail_bp)
        monkeypatch.setattr("app.routes.users.db", database)
        monkeypatch.setattr("app.database.db", database)
        monkeypatch.setattr("app.routes.urls.create_event", lambda *a, **kw: None)

        @instance.errorhandler(HTTPException)
        def error(exc):
            return jsonify(error=exc.description), exc.code

        monkeypatch.setattr(cache, "get_l2", lambda: None)
        monkeypatch.setattr(cache, "_l2_fire_and_forget", lambda fn: None)
        cache._l1.clear()
        yield instance
        cache._l1.clear()
        database.close()


@pytest.fixture(autouse=True)
def clean_tables():
    # Overrides the parent integration fixture, which connects to PostgreSQL.
    pass
