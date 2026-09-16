"""Hermetic unit tests for the consumer heartbeat helper (#174).

Covers ``consumer/app.py`` ``_heartbeat_path``/``_beat`` only: no network,
no threads, no sleeps. Uses the same aliased-import fixture pattern as
``tests/test_consumer.py`` (``monkeypatch.syspath_prepend`` +
``spec_from_file_location`` under the names ``config``,
``url_create_handler``, ``consumer_app``) so the standalone consumer
package's top-level ``import config`` resolves without shadowing the Flask
``app`` package.
"""

import importlib.util
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

CONSUMER_DIR = Path(__file__).resolve().parent.parent / "consumer"
_CONSUMER_MODULE_NAMES = ("config", "url_create_handler", "consumer_app")
_MISSING = object()


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def consumer_modules(monkeypatch):
    """Load consumer sources under aliased names; undo path/module changes."""
    monkeypatch.syspath_prepend(str(CONSUMER_DIR))
    saved = {name: sys.modules.get(name, _MISSING) for name in _CONSUMER_MODULE_NAMES}
    try:
        config_mod = _load_module("config", CONSUMER_DIR / "config.py")
        handler_mod = _load_module("url_create_handler", CONSUMER_DIR / "url_create_handler.py")
        app_mod = _load_module("consumer_app", CONSUMER_DIR / "app.py")
    except Exception:
        for name, mod in saved.items():
            if mod is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        raise
    try:
        yield SimpleNamespace(app=app_mod, handler=handler_mod, config=config_mod)
    finally:
        for name, mod in saved.items():
            if mod is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def test_heartbeat_path_default_includes_consumer_type(consumer_modules, monkeypatch):
    monkeypatch.delenv("CONSUMER_HEARTBEAT_FILE", raising=False)
    monkeypatch.setattr(consumer_modules.app.config, "CONSUMER_TYPE", "logs")
    assert consumer_modules.app._heartbeat_path() == "/tmp/consumer-logs.heartbeat"


def test_beat_writes_fresh_heartbeat_file(consumer_modules, monkeypatch, tmp_path):
    target = tmp_path / "consumer-logs.heartbeat"
    monkeypatch.setenv("CONSUMER_HEARTBEAT_FILE", str(target))
    before = time.time()
    consumer_modules.app._beat()
    assert target.exists()
    mtime = os.path.getmtime(target)
    assert mtime >= before - 1
    assert float(target.read_text()) >= before - 1


def test_beat_never_raises(consumer_modules, monkeypatch):
    """A heartbeat failure must never crash a consumer loop (#174)."""
    monkeypatch.setattr("builtins.open", MagicMock(side_effect=OSError("disk read-only")))
    consumer_modules.app._beat()  # must not raise
