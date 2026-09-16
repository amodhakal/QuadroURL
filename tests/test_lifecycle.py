"""Tests for app/lifecycle.py (#148, #167).

Pin start_background_workers without leaving stray threads: the sampler's
own duplicate-guard is exercised with the real (daemon) sampler exactly
once, while alerting/flush are stubbed via monkeypatch. No sleeps, no
network, env vars restored by monkeypatch.
"""

import threading

import pytest

import app.lifecycle as lifecycle


@pytest.fixture()
def _clean_lifecycle(monkeypatch):
    """Reset the atexit-once flag and alert env; monkeypatch restores both."""
    monkeypatch.setattr(lifecycle, "_atexit_registered", False)
    monkeypatch.delenv("ALERT_MONITOR_ENABLED", raising=False)
    monkeypatch.delenv("APP_URL", raising=False)
    yield


def _sampler_threads():
    return [t for t in threading.enumerate() if t.name == "system-metrics-sampler" and t.is_alive()]


def test_sampler_starts_once_across_two_calls(_clean_lifecycle, monkeypatch):
    """Two calls start exactly one sampler thread (duplicate-guard, #129)."""
    import app.utils.alerts as alerts_mod

    monkeypatch.setattr(alerts_mod, "start_alerting", lambda *a, **k: None)
    monkeypatch.setattr("atexit.register", lambda fn: fn)

    lifecycle.start_background_workers()
    lifecycle.start_background_workers()

    assert len(_sampler_threads()) == 1


def test_alert_monitor_only_when_env_true(_clean_lifecycle, monkeypatch):
    import app
    import app.utils.alerts as alerts_mod

    calls = []
    monkeypatch.setattr(alerts_mod, "start_alerting", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(app, "start_system_metrics_sampler", lambda *a, **k: None)
    monkeypatch.setattr("atexit.register", lambda fn: fn)

    lifecycle.start_background_workers()
    assert calls == []

    monkeypatch.setenv("ALERT_MONITOR_ENABLED", "true")
    monkeypatch.setenv("APP_URL", "http://example.test")
    lifecycle.start_background_workers()

    assert len(calls) == 1
    assert calls[0][1]["app_url"] == "http://example.test"
    assert calls[0][1]["interval"] == 60


def test_atexit_flush_registered_once(_clean_lifecycle, monkeypatch):
    import app
    import app.utils.alerts as alerts_mod

    monkeypatch.setattr(alerts_mod, "start_alerting", lambda *a, **k: None)
    monkeypatch.setattr(app, "start_system_metrics_sampler", lambda *a, **k: None)
    registered = []
    monkeypatch.setattr("atexit.register", lambda fn: registered.append(fn))

    lifecycle.start_background_workers()
    lifecycle.start_background_workers()

    assert len(registered) == 1
    assert lifecycle._atexit_registered is True
