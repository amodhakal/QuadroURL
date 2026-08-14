"""Tests for Distributed Token Bucket Rate Limiting."""

import time
from flask import jsonify
from unittest.mock import MagicMock
import app.utils.ratelimit as ratelimit_module
from app.utils.ratelimit import rate_limit
from app.cache import get_l2


def test_rate_limit_allows_within_capacity(app):
    @rate_limit(capacity=3, refill_rate=10.0)
    def sample_view():
        return jsonify(status="ok")

    r = get_l2()
    if r:
        for k in r.keys("ratelimit:*"):
            r.delete(k)

    with app.test_request_context("/test"):
        res1 = sample_view()
        assert res1.status_code == 200
        assert "X-RateLimit-Limit" in res1.headers
        assert "X-RateLimit-Remaining" in res1.headers
        assert int(res1.headers["X-RateLimit-Limit"]) == 3

    with app.test_request_context("/test"):
        res2 = sample_view()
        assert res2.status_code == 200


def test_rate_limit_exceeds_capacity(app):
    @rate_limit(capacity=1, refill_rate=0.0)
    def sample_view():
        return jsonify(status="ok")

    r = get_l2()
    if r:
        for k in r.keys("ratelimit:*"):
            r.delete(k)

    with app.test_request_context("/test-exceed"):
        res1 = sample_view()
        assert res1.status_code == 200
        assert int(res1.headers["X-RateLimit-Remaining"]) == 0

    with app.test_request_context("/test-exceed"):
        res2 = sample_view()
        assert res2.status_code == 429
        data = res2.get_json()
        assert data["error"] == "Rate limit exceeded"
        assert "Retry-After" in res2.headers


def test_rate_limit_fail_open_on_redis_error(app, monkeypatch):
    @rate_limit(capacity=1, refill_rate=1.0)
    def sample_view():
        return jsonify(status="ok")

    monkeypatch.setattr("app.utils.ratelimit.get_l2", lambda: None)

    with app.test_request_context("/test-fail"):
        res = sample_view()
        assert res.status_code == 200
        assert res.get_json() == {"status": "ok"}


def test_rate_limit_fail_open_when_script_raises(app, monkeypatch):
    @rate_limit(capacity=1, refill_rate=1.0)
    def sample_view():
        return jsonify(status="ok")

    monkeypatch.setattr(
        "app.utils.ratelimit.get_l2", lambda: MagicMock()
    )

    def raising_script(client):
        raise Exception("boom")

    monkeypatch.setattr(
        "app.utils.ratelimit.get_script", raising_script
    )

    with app.test_request_context("/test-script-fail"):
        res = sample_view()
        assert res.status_code == 200
        assert res.get_json() == {"status": "ok"}


def test_rate_limit_retry_after_computation(app, monkeypatch):
    @rate_limit(capacity=1, refill_rate=1.0)
    def sample_view():
        return jsonify(status="ok")

    fake_script = MagicMock()
    fake_script.return_value = [0, 0.0]
    monkeypatch.setattr(
        "app.utils.ratelimit.get_script", lambda client: fake_script
    )
    monkeypatch.setattr(
        "app.utils.ratelimit.get_l2", lambda: MagicMock()
    )

    with app.test_request_context("/test-retry-after"):
        res = sample_view()
        assert res.status_code == 429
        assert res.headers["Retry-After"] == "1"
        assert res.get_json()["retry_after"] == 1
