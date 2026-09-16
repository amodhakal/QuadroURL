"""Timezone-awareness checks for model datetime defaults (#121).

Runs against the TESTING + sync-fallback Postgres (``DATABASE_PORT=5433``)
via the shared ``app`` fixture in ``tests/conftest.py``. ``RequestLog`` is
deliberately never written: its table is not in conftest's ``create_tables``
list, so those checks use field defaults + unsaved instances only. Postgres
``TIMESTAMP`` (no tz) strips ``tzinfo`` on re-fetch, so all awareness
assertions target in-memory values (defaults / pre-save attributes /
post-``save()`` instance), never re-selected rows.
"""

import importlib.util
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.event import Event
from app.models.request_log import RequestLog
from app.models.url import Url
from app.models.user import User

CONSUMER_DIR = Path(__file__).resolve().parent.parent / "consumer"

_AWARE_FIELDS = [
    (User, "created_at"),
    (Url, "created_at"),
    (Url, "updated_at"),
    (Event, "timestamp"),
    (RequestLog, "created_at"),
]


def _assert_aware(value):
    assert isinstance(value, datetime), f"expected datetime, got {value!r}"
    assert value.tzinfo == timezone.utc, f"expected tzinfo==timezone.utc, got {value!r}"


def test_field_defaults_are_aware_callables():
    """Defaults must be lambdas returning fresh aware datetimes (not frozen)."""
    for model, field_name in _AWARE_FIELDS:
        field = model._meta.fields[field_name]
        default = field.default
        assert callable(default), f"{model.__name__}.{field_name} default not callable"
        first, second = default(), default()
        _assert_aware(first)
        _assert_aware(second)


def test_unsaved_instances_have_aware_defaults():
    """Peewee populates defaults on instantiation; they must be UTC-aware."""
    user = User(username="tz-user", email="tz-user@example.com")
    _assert_aware(user.created_at)

    url = Url(
        user=1,
        short_code="tz0001",
        original_url="https://example.com",
        title="Example",
        is_active=True,
    )
    _assert_aware(url.created_at)
    _assert_aware(url.updated_at)

    event = Event(url=1, user=1, event_type="click", details="{}")
    _assert_aware(event.timestamp)

    # RequestLog: no DB write (table not managed by conftest); pre-save only.
    entry = RequestLog(method="GET", path="/r/abc", status_code=200, latency_ms=1.5)
    _assert_aware(entry.created_at)


def test_url_save_bumps_updated_at_aware(app, sample_user):
    """Url.save() must refresh updated_at with an aware timestamp."""
    with app.app_context():
        url = Url.create(
            user=sample_user,
            short_code="tzsave1",
            original_url="https://example.com",
            title="Example",
            is_active=True,
        )
        _assert_aware(url.created_at)
        _assert_aware(url.updated_at)
        previous = url.updated_at
        time.sleep(0.02)
        url.title = "Example v2"
        url.save()
        _assert_aware(url.updated_at)
        assert url.updated_at > previous


def _load_consumer_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(CONSUMER_DIR))
    saved = {}
    for name in ("config", "url_create_handler", "consumer_app"):
        saved[name] = sys.modules.get(name, None)
    try:
        specs = {
            "config": CONSUMER_DIR / "config.py",
            "url_create_handler": CONSUMER_DIR / "url_create_handler.py",
            "consumer_app": CONSUMER_DIR / "app.py",
        }
        loaded = {}
        for name, path in specs.items():
            spec = importlib.util.spec_from_file_location(name, str(path))
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
        return SimpleNamespace(
            config=loaded["config"],
            handler=loaded["url_create_handler"],
            app=loaded["consumer_app"],
        )
    except Exception:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        raise


def test_consumer_models_aware_and_generate_path_unaffected(monkeypatch):
    """Consumer model copies use aware defaults; generate path unchanged."""
    modules = _load_consumer_modules(monkeypatch)
    handler = modules.handler
    try:
        for model, field_name in (
            (handler.Url, "created_at"),
            (handler.Url, "updated_at"),
            (modules.app.RequestLog, "created_at"),
            (modules.app.Event, "timestamp"),
        ):
            default = model._meta.fields[field_name].default
            assert callable(default), f"{model.__name__}.{field_name} default not callable"
            _assert_aware(default())

        # Short-code generation unaffected.
        code = handler.generate_short_code()
        assert re.fullmatch(r"[A-Za-z0-9]{6}", code) is not None

        # Batch path unaffected: handler still passes the same six kwargs to
        # Url.create (timestamps come from model defaults, not the handler),
        # and the emitted event carries an aware created_at ISO string.
        created = SimpleNamespace(
            id=42,
            user_id=7,
            short_code="Ab3xYz",
            original_url="https://example.com",
            title="Example",
        )
        create_calls = []

        def fake_create(**kwargs):
            create_calls.append(kwargs)
            return created

        monkeypatch.setattr(handler.Url, "create", fake_create)
        db = MagicMock(name="fake_db")
        redis_client = MagicMock(name="redis_client")
        ok, events = handler.handle_url_create_batch(
            [
                {
                    "request_id": "r-tz",
                    "user_id": 7,
                    "original_url": "https://example.com",
                    "title": "Example",
                }
            ],
            db,
            redis_client,
        )
        assert ok is True
        assert set(create_calls[0]) == {
            "user_id",
            "short_code",
            "original_url",
            "title",
            "is_active",
            "request_id",
        }
        assert len(events) == 1
        parsed = datetime.fromisoformat(events[0]["created_at"])
        _assert_aware(parsed)
    finally:
        for name in ("config", "url_create_handler", "consumer_app"):
            sys.modules.pop(name, None)
