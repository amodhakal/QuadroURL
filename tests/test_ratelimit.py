"""Tests for Distributed Token Bucket Rate Limiting."""

import time
from flask import jsonify
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
