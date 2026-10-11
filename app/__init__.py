import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

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
from app.utils.kafka_producer import publish_log_event
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


def _env_int(names, default, minimum=1, maximum=2**31 - 1):
    for name in names:
        value = os.environ.get(name)
        if value is not None and value != "":
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Invalid {name} configuration") from exc
            if not minimum <= value <= maximum:
                raise RuntimeError(f"Invalid {name} configuration")
            return value
    return default


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Invalid {name} configuration")


def _is_production():
    environment = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "")).lower()
    return (
        environment in {"production", "prod"}
        or os.environ.get("FLASK_DEBUG", "").lower() == "false"
    )


# Baseline headers applied to every response (#303). The API is JSON-first, so
# the default policy denies every fetch; the handful of HTML routes
# (/dashboard, /docs) override the CSP with one that allows their CDN.
_SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    (
        "Content-Security-Policy",
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    ),
)

_HSTS_HEADER = ("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


def _security_headers(production):
    return _SECURITY_HEADERS + ((_HSTS_HEADER,) if production else ())


def _session_secret(production):
    value = os.environ.get("SESSION_SECRET") or os.environ.get("SECRET_KEY")
    if value:
        if production and (len(value) < 32 or value in {"change-me", "changeme", "secret", "dev"}):
            raise RuntimeError("SESSION_SECRET must be a strong configured value in production")
        return value
    if production:
        raise RuntimeError("SESSION_SECRET must be configured in production")
    return secrets.token_urlsafe(32)


def _configure_security(app):
    production = _is_production()
    session_secret = _session_secret(production)
    session_lifetime = _env_int(
        ("SESSION_LIFETIME_HOURS", "SESSION_TTL_HOURS"),
        8,
        maximum=24 * 30,
    )
    same_site = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax").strip().title()
    if same_site not in {"Lax", "Strict"}:
        raise RuntimeError("SESSION_COOKIE_SAMESITE must be Lax or Strict")
    cookie_secure = _env_bool("SESSION_COOKIE_SECURE", production)
    if production and not cookie_secure:
        raise RuntimeError("SESSION_COOKIE_SECURE must be enabled in production")
    max_body = _env_int(
        ("MAX_REQUEST_BODY_BYTES", "MAX_CONTENT_LENGTH"),
        6 * 1024 * 1024,
        maximum=64 * 1024 * 1024,
    )
    proxy_count = _env_int(
        ("TRUSTED_PROXY_COUNT", "TRUST_PROXY_COUNT"),
        0,
        minimum=0,
        maximum=10,
    )
    app.config.update(
        SECRET_KEY=session_secret,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE=same_site,
        SESSION_COOKIE_SECURE=cookie_secure,
        SESSION_COOKIE_NAME=os.environ.get("SESSION_COOKIE_NAME", "session"),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=session_lifetime),
        SESSION_REFRESH_EACH_REQUEST=False,
        SESSION_ALLOWED_ORIGINS=os.environ.get("SESSION_ALLOWED_ORIGINS", ""),
        MAX_CONTENT_LENGTH=max_body,
        TRUSTED_PROXY_COUNT=proxy_count,
    )
    if proxy_count:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_count,
            x_proto=proxy_count,
            x_host=proxy_count,
            x_port=proxy_count,
            x_prefix=proxy_count,
        )

    @app.before_request
    def enforce_request_body_limit():
        if (
            request.content_length is not None
            and request.content_length > app.config["MAX_CONTENT_LENGTH"]
        ):
            abort(413, description="Request body too large")

    @app.after_request
    def apply_security_headers(response):
        # ``setdefault`` so a route that deliberately chose a stricter or
        # otherwise different value (e.g. the QR ``sandbox`` policy) wins.
        # This runs last-registered-first, before ``track_metrics`` adds
        # X-Request-ID, and both only ever set their own header.
        for header, value in _security_headers(_is_production()):
            response.headers.setdefault(header, value)
        return response


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
    _configure_security(app)
    configure_logging(app)
    init_db(app)

    from app.utils.ratelimit import warn_if_disabled
    from app.utils.request_ctx import warn_if_proxy_untrusted

    warn_if_disabled()
    warn_if_proxy_untrusted()

    from app import models  # noqa: F401

    register_routes(app)

    # Observability endpoints poll themselves every few seconds; counting
    # them would inflate RPS/latency baselines (#135).
    _METRICS_EXCLUDED = frozenset(
        {
            "/health",
            "/ready",
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
            from app.db_readiness import check_writable_primary

            check_writable_primary(db)
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

    @app.errorhandler(401)
    def unauthorized(error):
        app.logger.warning(f"Unauthorized: {error.description}")
        return jsonify({"error": str(error.description)}), 401

    @app.errorhandler(403)
    @app.errorhandler(405)
    @app.errorhandler(413)
    @app.errorhandler(415)
    def api_client_error(error):
        return jsonify({"error": str(error.description)}), error.code

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

    # Background workers are NOT started here (#128, #129, #148):
    # call app.lifecycle.start_background_workers(app) explicitly from
    # the entry point (run.py) so importing/building the app in tests
    # never spawns threads or registers atexit handlers.

    return app
