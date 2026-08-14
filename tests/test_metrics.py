"""Tests for the /metrics endpoint and the in-memory metrics store."""

import collections

import app.metrics_store as ms

EXPECTED_KEYS = [
    "system",
    "latency",
    "traffic",
    "errors",
    "saturation",
    "uptime_seconds",
    "recent_requests",
]


def _reset_store():
    """Zero out the module-level metrics store state."""
    ms.request_log.clear()
    ms.traffic_by_endpoint.clear()
    ms.errors_by_status.clear()
    ms.total_requests = 0
    ms.total_errors = 0
    ms.active_requests = 0
    ms.peak_active_requests = 0


def test_get_metrics_endpoint(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.content_type == "application/json"
    data = response.get_json()
    for key in EXPECTED_KEYS:
        assert key in data
    assert "cpu_percent" in data["system"]
    assert "system_ram" in data["system"]
    assert "process_memory_mb" in data["system"]


def test_get_metrics_snapshot_empty():
    _reset_store()
    try:
        snapshot = ms.get_metrics_snapshot()
        assert snapshot["latency"] == {
            "avg_ms": 0,
            "p50_ms": 0,
            "p95_ms": 0,
            "p99_ms": 0,
            "max_ms": 0,
        }
        assert snapshot["errors"]["error_rate_percent"] == 0
        assert snapshot["traffic"]["requests_per_second"] == 0
        assert snapshot["recent_requests"] == []
    finally:
        _reset_store()


def test_get_metrics_snapshot_populated():
    _reset_store()
    try:
        latencies = [500, 400, 300, 200, 100]
        paths = ["/users", "/health", "/nonexistent", "/metrics", "/dashboard"]
        for lat, path in zip(latencies, paths):
            ms.request_log.append({
                "timestamp": 0.0,
                "method": "GET",
                "path": path,
                "status": 404 if path == "/nonexistent" else 200,
                "latency_ms": lat,
            })
        ms.total_requests = 5
        ms.total_errors = 1
        ms.traffic_by_endpoint["GET /users"] = 3
        ms.traffic_by_endpoint["GET /health"] = 1
        ms.traffic_by_endpoint["POST /users"] = 1
        ms.errors_by_status[404] = 1

        snapshot = ms.get_metrics_snapshot()

        assert snapshot["latency"]["avg_ms"] == 300.0
        assert snapshot["latency"]["p50_ms"] == 300
        assert snapshot["latency"]["p95_ms"] == 500
        assert snapshot["latency"]["p99_ms"] == 500
        assert snapshot["latency"]["max_ms"] == 500

        assert snapshot["traffic"]["total_requests"] == 5
        top = snapshot["traffic"]["top_endpoints"]
        assert top[0] == {"endpoint": "GET /users", "count": 3}
        assert len(top) == 3

        assert snapshot["errors"]["total_errors"] == 1
        assert snapshot["errors"]["error_rate_percent"] == 20.0
        assert snapshot["errors"]["by_status"] == {404: 1}

        assert len(snapshot["recent_requests"]) == 5
        assert snapshot["recent_requests"][0]["path"] == "/dashboard"
        assert [r["latency_ms"] for r in snapshot["recent_requests"]] == [
            100,
            200,
            300,
            400,
            500,
        ]
    finally:
        _reset_store()
