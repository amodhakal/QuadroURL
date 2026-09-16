"""Prometheus metrics (per-request counters + process gauges).

Aggregation across gunicorn workers (#136): ``prometheus_client`` keeps
metrics in-process, so with 4 workers each ``/prometheus-metrics`` scrape
only saw ~1/4 of the traffic and the dashboard under-reported. When
``PROMETHEUS_MULTIPROC_DIR`` is set (see ``gunicorn.conf.py`` + Dockerfile),
metrics are file-backed and scrapes aggregate all live workers via
``MultiProcessCollector``:

- Counters/Histograms aggregate by summing (client default).
- ``http_requests_in_progress`` uses ``livesum``: the true total in flight.
- ``process_cpu_percent`` uses ``livemax`` (hottest live worker) and
  ``process_memory_mb`` uses ``livesum`` (total live RSS): single series so
  the existing dashboard exprs keep working. The ``live*`` prefix matters:
  ``mark_process_dead`` (gunicorn ``child_exit`` hook) only reaps ``live*``
  gauge files — counters/histograms correctly persist (cumulative), while a
  plain max/sum gauge would linger with a dead worker's last value.

Without the env var everything behaves exactly as before (default
registry, plain gauges), which is also what the test suite exercises.
Dead-worker files are reaped by the ``child_exit`` hook in
``gunicorn.conf.py``; without it, exited workers' last values would linger
in scrapes.
"""

import os

from flask import Blueprint, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)

prometheus_bp = Blueprint("prometheus", __name__)

_MULTIPROC_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR")

if _MULTIPROC_DIR:
    _REGISTRY = CollectorRegistry()
    multiprocess.MultiProcessCollector(_REGISTRY)
    _IN_PROGRESS_MODE = "livesum"
    _CPU_MODE = "livemax"
    _MEMORY_MODE = "livesum"
else:
    _REGISTRY = REGISTRY
    _IN_PROGRESS_MODE = None
    _CPU_MODE = None
    _MEMORY_MODE = None


def _gauge(name, documentation, mode):
    if mode is None:
        return Gauge(name, documentation, registry=_REGISTRY)
    return Gauge(name, documentation, multiprocess_mode=mode, registry=_REGISTRY)


REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
    registry=_REGISTRY,
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    registry=_REGISTRY,
)

REQUESTS_IN_PROGRESS = _gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    _IN_PROGRESS_MODE,
)

ERROR_COUNT = Counter(
    "http_errors_total",
    "Total HTTP error responses",
    ["method", "endpoint", "status"],
    registry=_REGISTRY,
)

CPU_USAGE = _gauge("process_cpu_percent", "Current CPU usage percentage", _CPU_MODE)
MEMORY_USAGE_MB = _gauge("process_memory_mb", "Process RSS memory in MB", _MEMORY_MODE)


@prometheus_bp.route("/prometheus-metrics")
def prometheus_metrics():
    return Response(generate_latest(_REGISTRY), mimetype=CONTENT_TYPE_LATEST)
