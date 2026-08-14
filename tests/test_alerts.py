"""Tests for app/utils/alerts.py — Discord alert sending and health monitor."""

import pytest
from unittest.mock import MagicMock

import app.utils.alerts as alerts


@pytest.fixture(autouse=True)
def _reset_discord_webhook(monkeypatch):
    """Ensure DISCORD_WEBHOOK_URL is unset and mocks are clean for each test."""
    monkeypatch.setattr(alerts, "DISCORD_WEBHOOK_URL", "")
    yield
    monkeypatch.setattr(alerts, "DISCORD_WEBHOOK_URL", "")


def test_send_alert_no_webhook_returns_none(monkeypatch):
    monkeypatch.setattr(alerts, "DISCORD_WEBHOOK_URL", "")
    assert alerts.send_alert("title", "message") is None


def test_send_alert_success_with_critical_and_warning_colors(monkeypatch):
    monkeypatch.setattr(alerts, "DISCORD_WEBHOOK_URL", "https://example.com/webhook")
    fake_requests = MagicMock()
    fake_requests.post.return_value = MagicMock()
    monkeypatch.setattr(alerts, "requests", fake_requests)

    alerts.send_alert("Down", "boom", level="critical")
    alerts.send_alert("Up", "back", level="warning")

    critical_call, warning_call = fake_requests.post.call_args_list
    assert critical_call.kwargs["timeout"] == 5
    assert critical_call.kwargs["json"]["embeds"][0]["title"] == "🚨 Down"
    assert critical_call.kwargs["json"]["embeds"][0]["color"] == 16711680
    assert warning_call.kwargs["json"]["embeds"][0]["color"] == 16776960


def test_send_alert_swallows_post_exception(monkeypatch):
    monkeypatch.setattr(alerts, "DISCORD_WEBHOOK_URL", "https://example.com/webhook")
    fake_requests = MagicMock()
    fake_requests.post.side_effect = Exception("boom")
    monkeypatch.setattr(alerts, "requests", fake_requests)

    alerts.send_alert("title", "message")
    assert True


def test_start_alerting_spawns_daemon_thread(monkeypatch):
    fake_thread = MagicMock()
    fake_thread.start.return_value = None
    fake_threading = MagicMock()
    fake_threading.Thread = MagicMock()
    fake_threading.Thread.return_value = fake_thread
    monkeypatch.setattr(alerts, "threading", fake_threading)

    alerts.start_alerting(app_url="http://myapp", interval=1)

    alerts.threading.Thread.assert_called_once_with(
        target=alerts._monitor,
        args=("http://myapp", 1),
        daemon=True,
    )
    assert fake_thread.start.called


def test_monitor_down_then_recovered_series(monkeypatch):
    fake_requests = MagicMock()

    def fake_get(url, timeout):
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(alerts, "requests", fake_requests)
    monkeypatch.setattr(alerts, "send_alert", MagicMock())

    calls = {"n": 0}

    def fake_sleep(interval):
        calls["n"] += 1
        if calls["n"] >= 4:
            raise RuntimeError("stop")

    monkeypatch.setattr(alerts.time, "sleep", fake_sleep)

    seq_results = [_resp(500), Exception("boom"), _resp(200)]
    seq_index = {"i": 0}

    def fake_get_sequence(url, timeout):
        item = seq_results[seq_index["i"]]
        seq_index["i"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(alerts.requests, "get", fake_get_sequence)

    with pytest.raises(RuntimeError):
        alerts._monitor("http://myapp", interval=1)

    call_args = list(alerts.send_alert.call_args_list)
    assert call_args[0].args[0] == "Service Down"
    assert call_args[0].kwargs["level"] == "critical"
    assert call_args[1].args[0] == "Service Recovered"
    assert call_args[1].kwargs["level"] == "warning"


def _resp(status_code):
    resp = MagicMock()
    resp.status_code = status_code
    return resp
