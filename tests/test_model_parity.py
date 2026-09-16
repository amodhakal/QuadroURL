"""Guard against app/consumer model drift (#144).

Both services use shared.schema.create_models with independent database
bindings. These checks exercise the live imports to guard against accidentally
reintroducing a duplicate model. Foreign-key ID accessors preserve the consumer
payload contract without importing the Flask application.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.event import Event as AppEvent
from app.models.request_log import RequestLog as AppRequestLog
from app.models.url import Url as AppUrl

CONSUMER_DIR = Path(__file__).resolve().parent.parent / "consumer"

# (app model, consumer module file, consumer model attr). ``app.models.User``
# has no consumer twin (consumers only ever store raw ``user_id`` ints).
PAIRS = (
    (AppUrl, "url_create_handler.py", "Url"),
    (AppEvent, "app.py", "Event"),
    (AppRequestLog, "app.py", "RequestLog"),
)

# Consumer twins use raw ints where the app uses foreign keys. Normalize
# both to IntegerField so the comparison checks the storage type, which is
# what must agree. (All app FKs point at AutoField PKs, i.e. ints.)
_FK = "ForeignKeyField"
_INT = "IntegerField"


def _load_consumer_modules():
    """Load consumer sources under aliased names (mirrors test_consumer.py).

    The consumer entry point is named ``app``, which collides with the Flask
    ``app`` package, so sources are loaded from file locations and unloaded
    afterwards.
    """
    sys.path.insert(0, str(CONSUMER_DIR))
    names = ("parity_consumer_app", "parity_url_create_handler")
    saved = {name: sys.modules.get(name, None) for name in names}
    try:
        modules = {}
        for name, filename in (
            ("parity_consumer_app", "app.py"),
            ("parity_url_create_handler", "url_create_handler.py"),
        ):
            spec = importlib.util.spec_from_file_location(name, str(CONSUMER_DIR / filename))
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            modules[name] = module
        return SimpleNamespace(
            Event=modules["parity_consumer_app"].Event,
            RequestLog=modules["parity_consumer_app"].RequestLog,
            Url=modules["parity_url_create_handler"].Url,
        )
    finally:
        sys.path.remove(str(CONSUMER_DIR))
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


@pytest.fixture(autouse=True)
def clean_tables():
    """Schema inspection does not require the integration database fixture."""
    yield


@pytest.fixture(scope="module")
def consumer_models():
    return _load_consumer_modules()


def _columns(model):
    """Map db column name -> (storage type, null, char max_length)."""
    out = {}
    for db_column, field in model._meta.columns.items():
        kind = type(field).__name__
        if kind == _FK:
            kind = _INT
        out[db_column] = (kind, bool(field.null), getattr(field, "max_length", None))
    return out


def _consumer_model(modules, app_model, attr):
    return getattr(modules, attr)


def test_table_names_match(consumer_models):
    for app_model, _, attr in PAIRS:
        consumer_model = _consumer_model(consumer_models, app_model, attr)
        assert consumer_model._meta.table_name == app_model._meta.table_name, (
            f"{attr}: consumer table {consumer_model._meta.table_name!r} != "
            f"app table {app_model._meta.table_name!r}"
        )


def test_column_parity(consumer_models):
    for app_model, _, attr in PAIRS:
        consumer_model = _consumer_model(consumer_models, app_model, attr)
        app_cols = _columns(app_model)
        consumer_cols = _columns(consumer_model)
        assert set(consumer_cols) == set(app_cols), (
            f"{attr}: column mismatch: "
            f"consumer-only={sorted(set(consumer_cols) - set(app_cols))} "
            f"app-only={sorted(set(app_cols) - set(consumer_cols))}"
        )
        for column, spec in app_cols.items():
            assert consumer_cols[column] == spec, (
                f"{attr}.{column}: consumer {consumer_cols[column]} != app {spec}"
            )
