"""Tests for the /logs endpoint."""

import pytest

from app.log_store import log_records


@pytest.fixture(autouse=True)
def clear_log_records():
    """Clear the in-memory log buffer before and after every test."""
    log_records.clear()
    yield
    log_records.clear()


def test_logs_returns_200(admin_client):
    response = admin_client.get("/logs")
    assert response.status_code == 200


def test_logs_returns_json(admin_client):
    response = admin_client.get("/logs")
    assert response.content_type == "application/json"


def test_logs_response_has_logs_key(admin_client):
    response = admin_client.get("/logs")
    data = response.get_json()
    assert "logs" in data


def test_logs_empty_when_no_records(admin_client):
    """The request to /logs itself may generate log entries via middleware."""
    response = admin_client.get("/logs")
    data = response.get_json()
    for entry in data["logs"]:
        assert entry["path"] == "/logs"


def test_logs_returns_captured_records(admin_client):
    log_records.append({"level": "INFO", "message": "hello world"})
    response = admin_client.get("/logs")
    data = response.get_json()
    messages = [entry["message"] for entry in data["logs"]]
    assert "hello world" in messages


def test_logs_caps_at_50_most_recent_records(admin_client):
    for i in range(75):
        log_records.append({"level": "INFO", "message": f"message {i}"})

    response = admin_client.get("/logs")
    data = response.get_json()
    assert len(data["logs"]) == 50
    messages = [entry["message"] for entry in data["logs"]]
    assert "message 74" in messages
    assert "message 0" not in messages


def test_logs_returns_list_under_logs_key(admin_client):
    response = admin_client.get("/logs")
    data = response.get_json()
    assert isinstance(data["logs"], list)


def test_logs_only_allows_get(admin_client):
    """POST, PUT, DELETE should not be allowed on /logs."""
    for method in [admin_client.post, admin_client.put, admin_client.delete]:
        response = method("/logs")
        assert response.status_code == 405
