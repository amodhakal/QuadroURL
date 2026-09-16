import os
import re
import threading
import time
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
from app.utils.logger import JsonFormatter, ListHandler, configure_logging


__all__ = ["JsonFormatter", "ListHandler", "configure_logging", "create_app", "log_records"]


def _sample_system_metrics():
    """Sample CPU/memory into the Prometheus gauges.

    Kept as a plain function so the background thread can loop over it and
    tests can invoke it directly.
    """
    process = psutil.Process(os.getpid())
    cpu = psutil.cpu_percent(interval=None)
    CPU_USAGE.set(cpu if cpu is not None else 0.0)
    MEMORY_USAGE_MB.set(round(process.memory_info().rss / 1024 / 1024, 1))


def start_system_metrics_sampler(interval=5):
    """Update CPU/memory gauges from a background thread.

    Keeps psutil syscalls out of the request hot path. Guarded against
    duplicate starts when create_app() is called repeatedly (tests,
    reloader) — see #129.
    """
    for t in threading.enumerate():
        if t.name == "system-metrics-sampler" and t.is_alive():
            return t

    def _run():
        while True:
            try:
                _sample_system_metrics()
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=_run, name="system-metrics-sampler", daemon=True)
    t.start()
    return t


_kafka_check_cache = {"result": None, "at": 0.0}
_kafka_check_lock = threading.Lock()
KAFKA_CHECK_TTL_S = 10.0


def _cached_kafka_check(app, get_producer_fn):
    """Lightweight cached Kafka readiness probe (#140)."""
    now = time.monotonic()
    with _kafka_check_lock:
        if (
            now - _kafka_check_cache["at"] < KAFKA_CHECK_TTL_S
            and _kafka_check_cache["result"] is not None
        ):
            return _kafka_check_cache["result"]
    try:
        get_producer_fn().list_topics(timeout=2)
        result = "ok"
    except Exception as exc:
        app.logger.warning(f"Readiness kafka check failed: {exc}")
        result = "unavailable"
    with _kafka_check_lock:
        _kafka_check_cache["result"] = result
        _kafka_check_cache["at"] = now
    return result


def create_app():
    load_dotenv()
    app = Flask(__name__)
    configure_logging(app)
    init_db(app)

    from app import models  # noqa: F401

    register_routes(app)

    # Observability endpoints poll themselves every few seconds; counting
    # them would inflate RPS/latency baselines (#135).
    _METRICS_EXCLUDED = frozenset(
        {
            "/health",
            "/metrics",
            "/logs",
            "/dashboard",
            "/prometheus-metrics",
        }
    )

    @app.before_request
    def log_request():
        if request.path in _METRICS_EXCLUDED:
            return
        from app.utils.request_ctx import get_request_id

        request._start_time = time.perf_counter()
        get_request_id()
        record_request_start()
        REQUESTS_IN_PROGRESS.inc()

    @app.after_request
    def track_metrics(response):
        if request.path in _METRICS_EXCLUDED:
            return response
        try:
            from app.utils.request_ctx import get_client_ip, get_request_id

            try:
                response.headers["X-Request-ID"] = get_request_id()
            except Exception:
                pass
            start = getattr(request, "_start_time", None)
            latency_s = time.perf_counter() - start if start is not None else 0.0
            latency_ms = latency_s * 1000
            # Bound cardinality: use the matched route template, not the raw
            # path with IDs/codes (#123, #154).
            try:
                endpoint = (
                    request.url_rule.rule
                    if request.url_rule
                    else (request.endpoint or request.path)
                )
            except Exception:
                endpoint = request.endpoint or request.path
            record_request_end(request.method, endpoint, response.status_code, latency_ms)

            REQUEST_COUNT.labels(
                method=request.method, endpoint=endpoint, status=response.status_code
            ).inc()
            REQUEST_LATENCY.labels(method=request.method, endpoint=endpoint).observe(latency_s)

            if response.status_code >= 400:
                ERROR_COUNT.labels(
                    method=request.method, endpoint=endpoint, status=response.status_code
                ).inc()

            short_code = ""
            sc_match = re.match(r"^/r/([^/]+)$", request.path) or re.match(
                r"^/urls/([^/]+)/redirect$", request.path
            )
            if sc_match:
                short_code = sc_match.group(1)

            client_ip = get_client_ip()
            user_agent = request.headers.get("User-Agent", "")

            try:
                publish_log_event(
                    {
                        "user_agent": user_agent,
                        "client_ip": client_ip,
                        "method": request.method,
                        "path": request.path,
                        "status_code": response.status_code,
                        "latency_ms": round(latency_ms, 2),
                        "short_code": short_code,
                        "request_id": get_request_id(),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception:
                app.logger.exception("Failed to publish request log to Kafka")
        finally:
            REQUESTS_IN_PROGRESS.dec()

        return response

    @app.route("/health")
    def health():
        return jsonify(status="ok")

    @app.route("/ready")
    def readiness():
        from app.cache import get_l2
        from app.database import db
        from app.utils.kafka_producer import get_producer

        checks = {}

        try:
            db.execute_sql("SELECT 1")
            checks["postgres"] = "ok"
        except Exception as exc:
            app.logger.warning(f"Readiness postgres check failed: {exc}")
            checks["postgres"] = "unavailable"

        try:
            redis_client = get_l2()
            if redis_client is not None and redis_client.ping():
                checks["redis"] = "ok"
            else:
                checks["redis"] = "unavailable"
        except Exception as exc:
            app.logger.warning(f"Readiness redis check failed: {exc}")
            checks["redis"] = "unavailable"

        # Kafka metadata can block up to the timeout; cache the result
        # briefly so a transient blip doesn't flap LB membership (#140).
        checks["kafka"] = _cached_kafka_check(app, get_producer)

        ready = all(v == "ok" for v in checks.values())
        return jsonify(status="ok" if ready else "not_ready", checks=checks), (
            200 if ready else 503
        )

    @app.errorhandler(400)
    def bad_request(error):
        app.logger.warning(f"Bad request: {error.description}")
        return jsonify({"error": str(error.description)}), 400

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(422)
    def unprocessable_entity(error):
        app.logger.warning("Unprocessable entity")
        return jsonify({"error": "Unprocessable entity"}), 422

    @app.errorhandler(500)
    def internal_server_error(error):
        app.logger.exception("Internal server error")
        # Intentional abort(500, description=...) messages are static and
        # safe to surface (e.g. short-code exhaustion). Anything else —
        # including wrapped unhandled exceptions — gets a generic message
        # so internals never leak (#104).
        from werkzeug.exceptions import HTTPException, InternalServerError

        if (
            isinstance(error, HTTPException)
            and error.description
            and error.description != InternalServerError.description
        ):
            return jsonify({"error": error.description}), 500
        return jsonify({"error": "Internal server error"}), 500

    @app.errorhandler(503)
    def service_unavailable(error):
        return jsonify({"error": str(error.description)}), 503

    # Background workers are opt-in and started once (#128, #129):
    # - the Discord monitor cannot detect a real crash from inside the
    #   same process, so it only runs when ALERT_MONITOR_ENABLED=true;
    # - the sampler is guarded internally against duplicate threads;
    # - atexit flush is registered once per process.
    from app.utils.alerts import start_alerting
    from app.utils.kafka_producer import flush_producer, publish_log_event

    if os.environ.get("ALERT_MONITOR_ENABLED", "false").lower() == "true":
        app_url = os.environ.get("APP_URL", "http://127.0.0.1:5000")
        start_alerting(app_url=app_url, interval=60)

    start_system_metrics_sampler()

    import atexit

    if not getattr(create_app, "_atexit_registered", False):
        atexit.register(flush_producer)
        create_app._atexit_registered = True

    return app
