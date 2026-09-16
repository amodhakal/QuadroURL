"""Gunicorn config: lifecycle hooks only.

Bind/workers/threads/timeout stay on the command line (Dockerfile CMD) so
deploys keep overriding them without editing this file.

Hook: reap dead prometheus multiprocess files (#136). Each worker writes its
metrics to PROMETHEUS_MULTIPROC_DIR; without cleanup, an exited worker's
last values linger in scrapes forever. Validate with:
    uv run gunicorn --check-config --config gunicorn.conf.py run:app
"""

import os
import shutil


def on_starting(server):
    """Wipe the multiprocess dir before workers fork (no readers yet)."""
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if multiproc_dir:
        shutil.rmtree(multiproc_dir, ignore_errors=True)
        os.makedirs(multiproc_dir, exist_ok=True)


def child_exit(server, worker):
    """Drop a dead worker's metrics files so scrapes stop including it."""
    if not os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        return
    try:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)
    except Exception:
        server.log.exception("Failed to reap prometheus multiprocess files")
