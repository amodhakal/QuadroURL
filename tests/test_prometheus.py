"""Tests for the /prometheus-metrics endpoint."""

from app.routes.prometheus import CONTENT_TYPE_LATEST


def test_prometheus_metrics(client):
    response = client.get("/prometheus-metrics")
    assert response.status_code == 200
    assert response.content_type.startswith("text/plain")
    assert "text/plain" in response.content_type
    body = response.get_data(as_text=True)
    assert body
    assert "http_requests_total" in body
