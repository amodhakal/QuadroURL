import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request

import psutil

from app.database import init_db
from app.log_store import log_records
from app.metrics_store import record_request_end, record_request_start
from app.routes import register_routes
from app.routes.prometheus import (
    CPU_USAGE,
    ERROR_COUNT,
    MEMORY_USAGE_MB,
    REQUEST_COUNT,
    REQUEST_LATENCY,
    REQUESTS_IN_PROGRESS,
)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        try:
            log_data["method"] = request.method
            log_data["path"] = request.path
            log_data["remote_addr"] = request.headers.get(
                "X-Forwarded-For", request.remote_addr
            )
        except RuntimeError:
            pass
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


class ListHandler(logging.Handler):
    """In-memory log handler that stores records as structured dicts.

    Delegates exception formatting to a ``logging.Formatter`` instance so
    that ``formatException`` is available (it is defined on ``Formatter``,
    not ``Handler``).
    """

    _formatter = logging.Formatter()

    def emit(self, record):
        try:
            log_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            try:
                log_data["method"] = request.method
                log_data["path"] = request.path
                log_data["remote_addr"] = request.headers.get(
                    "X-Forwarded-For", request.remote_addr
                )
            except RuntimeError:
                pass
            if record.exc_info:
                log_data["exception"] = self._formatter.formatException(
                    record.exc_info
                )
            log_records.append(log_data)
            if len(log_records) > 200:
                del log_records[:-200]
        except Exception:
            self.handleError(record)


def configure_logging(app):
    log_level = (
        logging.DEBUG
        if os.environ.get("LOG_LEVEL", "").upper() == "DEBUG"
        else logging.INFO
    )
    json_handler = logging.StreamHandler()
    json_handler.setFormatter(JsonFormatter())
    list_handler = ListHandler()

    app.logger.handlers.clear()
    app.logger.addHandler(json_handler)
    app.logger.addHandler(list_handler)
    app.logger.setLevel(log_level)
    app.logger.propagate = False

    for name in ("", "werkzeug", "peewee"):
        logger = logging.getLogger(name) if name else logging.getLogger()
        if not logger.handlers:
            logger.addHandler(json_handler)
            logger.addHandler(list_handler)
            logger.setLevel(log_level)
            if name:
                logger.propagate = False


def create_app():
    load_dotenv()
    app = Flask(__name__)
    configure_logging(app)
    init_db(app)

    from app import models  # noqa: F401

    register_routes(app)

    @app.before_request
    def log_request():
        request._start_time = time.time()
        record_request_start()
        REQUESTS_IN_PROGRESS.inc()
        app.logger.info("Request received")

    @app.after_request
    def track_metrics(response):
        latency_s = time.time() - getattr(request, "_start_time", time.time())
        latency_ms = latency_s * 1000
        record_request_end(request.method, request.path, response.status_code, latency_ms)

        endpoint = request.path
        REQUEST_COUNT.labels(method=request.method, endpoint=endpoint, status=response.status_code).inc()
        REQUEST_LATENCY.labels(method=request.method, endpoint=endpoint).observe(latency_s)
        REQUESTS_IN_PROGRESS.dec()

        if response.status_code >= 400:
            ERROR_COUNT.labels(method=request.method, endpoint=endpoint, status=response.status_code).inc()

        process = psutil.Process(os.getpid())
        CPU_USAGE.set(psutil.cpu_percent(interval=None))
        MEMORY_USAGE_MB.set(round(process.memory_info().rss / 1024 / 1024, 1))

        return response

    @app.route("/health")
    def health():
        app.logger.info("Health check requested")
        return jsonify(status="ok")

    @app.errorhandler(400)
    def bad_request(error):
        app.logger.warning(f"Bad request: {error.description}")
        return jsonify({"error": str(error.description)}), 400

    @app.errorhandler(404)
    def not_found(error):
        app.logger.warning("Route not found")
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(422)
    def unprocessable_entity(error):
        app.logger.warning("Unprocessable entity")
        return jsonify({"error": "Unprocessable entity"}), 422

    @app.errorhandler(500)
    def internal_server_error(error):
        app.logger.exception("Internal server error")
        _, exc, _ = sys.exc_info()
        return jsonify({"error": str(exc)}), 500

    # Start Discord alert monitor in background
    from app.utils.alerts import start_alerting
    app_url = os.environ.get("APP_URL", "http://127.0.0.1:5000")
    start_alerting(app_url=app_url, interval=60)

    return app
