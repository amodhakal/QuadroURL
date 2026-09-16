"""Explicit background-thread lifecycle (#148).

create_app() is side-effect free: importing the app or building it in
tests must never spawn threads. Entry points (run.py, gunicorn `run:app`)
opt in by calling start_background_workers(app) once at startup.
"""

import atexit
import os

__all__ = ["start_background_workers"]

_atexit_registered = False


def start_background_workers(app=None):
    """Start alert monitor, metrics sampler, producer flush, cache bus (#128, #129, #118).

    - the Discord monitor cannot detect a real crash from inside the
      same process, so it only runs when ALERT_MONITOR_ENABLED=true;
    - the sampler is guarded internally against duplicate threads;
    - atexit flush is registered once per process.
    Idempotent: safe to call more than once (duplicate-start guards).
    """
    from app import start_system_metrics_sampler
    from app.cache import start_invalidation_listener
    from app.utils.alerts import start_alerting
    from app.utils.kafka_producer import flush_producer

    if os.environ.get("ALERT_MONITOR_ENABLED", "false").lower() == "true":
        app_url = os.environ.get("APP_URL", "http://127.0.0.1:5000")
        start_alerting(app_url=app_url, interval=60)

    start_system_metrics_sampler()

    start_invalidation_listener()

    global _atexit_registered
    if not _atexit_registered:
        atexit.register(flush_producer)
        _atexit_registered = True
