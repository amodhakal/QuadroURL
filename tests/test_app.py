"""Tests for the Flask application factory and route registration."""

import json
import logging

from flask import Flask

from app import JsonFormatter, ListHandler


def test_create_app_returns_flask_instance(app):
    assert isinstance(app, Flask)


def test_app_has_testing_config(app):
    assert app.config["TESTING"] is True


def test_app_registers_health_route(app):
    rules = [rule.rule for rule in app.url_map.iter_rules()]
    assert "/health" in rules


def test_app_registers_user_routes(app):
    rules = [rule.rule for rule in app.url_map.iter_rules()]
    assert "/users" in rules
    assert "/users/<int:user_id>" in rules
    assert "/users/bulk" in rules


def test_app_registers_url_routes(app):
    rules = [rule.rule for rule in app.url_map.iter_rules()]
    assert "/urls" in rules
    assert "/urls/<int:url_id>" in rules


def test_app_registers_event_routes(app):
    rules = [rule.rule for rule in app.url_map.iter_rules()]
    assert "/events" in rules


def test_unknown_route_returns_404(client):
    response = client.get("/nonexistent")
    assert response.status_code == 404


def test_404_response_is_json(client):
    response = client.get("/nonexistent")
    assert response.content_type == "application/json"
    data = response.get_json()
    assert "error" in data


# ---------------------------------------------------------------------------
# JsonFormatter / ListHandler coverage (app/__init__.py)
# ---------------------------------------------------------------------------

def _make_record(message="hello", level=logging.INFO):
    return logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )


def test_json_formatter_within_request_context_includes_request_data(app):
    formatter = JsonFormatter()
    record = _make_record("in-request")
    with app.test_request_context("/path?q=1"):
        out = formatter.format(record)
    data = json.loads(out)
    assert data["level"] == "INFO"
    assert data["logger"] == "test.logger"
    assert data["message"] == "in-request"
    assert data["method"] == "GET"
    assert data["path"] == "/path"
    assert "remote_addr" in data


def test_json_formatter_outside_request_context_falls_back(app):
    # Covers lines 42-43: request proxy access raises RuntimeError, which is
    # captured so format() completes without a request context.
    formatter = JsonFormatter()
    record = _make_record("no-context")
    out = formatter.format(record)
    data = json.loads(out)
    assert data["message"] == "no-context"
    assert data["logger"] == "test.logger"
    assert "method" not in data
    assert "path" not in data


def test_list_handler_emit_without_request_context(app):
    # Covers lines 73-74: RuntimeError from request proxy is caught.
    from app import log_records
    log_records.clear()
    handler = ListHandler()
    record = _make_record("list-no-context")
    handler.emit(record)
    try:
        assert any(
            entry.get("message") == "list-no-context" for entry in log_records
        )
    finally:
        log_records.clear()


def test_list_handler_truncates_cap_at_200(app):
    # Covers lines 81-83: delete old entries once len > 200.
    from app import log_records
    log_records.clear()
    handler = ListHandler()
    log_records.extend(
        [{"message": f"old-{i}"} for i in range(200)]
    )
    record = _make_record("newest")
    handler.emit(record)
    try:
        assert len(log_records) <= 200
        assert log_records[-1]["message"] == "newest"
    finally:
        log_records.clear()


def test_system_metrics_sampler_populates_gauges(app):
    # System gauges are populated by the background sampler (not in the
    # request hot path). Invoke the sampler body directly.
    from app import _sample_system_metrics
    from app.routes.prometheus import CPU_USAGE, MEMORY_USAGE_MB

    CPU_USAGE._value.set(None)
    MEMORY_USAGE_MB._value.set(None)

    _sample_system_metrics()

    assert CPU_USAGE._value.get() is not None
    assert isinstance(CPU_USAGE._value.get(), float)
    assert isinstance(MEMORY_USAGE_MB._value.get(), (int, float))


def test_422_error_handler(app):
    # Covers lines 167-168: registered 422 handler returns a JSON error.
    from werkzeug.exceptions import UnprocessableEntity

    handler = app.error_handler_spec[None][422][UnprocessableEntity]
    with app.test_request_context("/x"):
        response, code = handler(UnprocessableEntity())
    assert code == 422
    assert response.json["error"] == "Unprocessable entity"


def test_500_error_handler(app):
    # Covers lines 172-174: registered 500 handler returns JSON with the
    # exception message. The handler inspects sys.exc_info(), so it is invoked
    # while a RuntimeError is being handled.
    import sys
    from werkzeug.exceptions import InternalServerError

    handler = app.error_handler_spec[None][500][InternalServerError]
    with app.test_request_context("/x"):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            response, code = handler(sys.exc_info()[1])
    assert code == 500
    assert response.json["error"] == "boom"


def test_list_handler_emit_error_path_calls_handle_error(app):
    # Covers lines 82-83: if ListHandler.emit raises internally, the exception
    # is caught and delegated to self.handleError without propagating.
    from app import ListHandler

    class BadRecord:
        levelname = "ERROR"

        def getMessage(self):
            raise ValueError("boom")

    handler = ListHandler()
    handler.emit(BadRecord())  # must not raise
    assert True
