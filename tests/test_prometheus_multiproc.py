"""Tests for multiprocess Prometheus aggregation across workers (#136).

Workers are simulated with subprocesses (fresh interpreters with
``PROMETHEUS_MULTIPROC_DIR`` set before import, exactly like gunicorn
workers); the parent then reads the aggregate through
``MultiProcessCollector``. No network, no gunicorn, no sleeps.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from prometheus_client import CollectorRegistry, multiprocess

REPO_ROOT = Path(__file__).resolve().parent.parent

WORKER_SCRIPT = textwrap.dedent(
    """
    from app.routes.prometheus import (
        CPU_USAGE,
        ERROR_COUNT,
        MEMORY_USAGE_MB,
        REQUEST_COUNT,
        REQUESTS_IN_PROGRESS,
    )

    REQUEST_COUNT.labels(method="GET", endpoint="/urls", status="200").inc()
    ERROR_COUNT.labels(method="GET", endpoint="/urls", status="500").inc()
    REQUESTS_IN_PROGRESS.inc()
    CPU_USAGE.set(12.5)
    MEMORY_USAGE_MB.set(100.0)
    """
)


def _run_worker(multiproc_dir):
    env = dict(
        os.environ,
        PROMETHEUS_MULTIPROC_DIR=str(multiproc_dir),
        PYTHONPATH=str(REPO_ROOT),
    )
    proc = subprocess.run(
        [sys.executable, "-c", WORKER_SCRIPT],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


def _collect(multiproc_dir):
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=str(multiproc_dir))
    samples = {}
    for family in registry.collect():
        for sample in family.samples:
            samples[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
    return samples


def test_workers_aggregate(tmp_path):
    _run_worker(tmp_path)
    _run_worker(tmp_path)
    assert any(tmp_path.iterdir()), "workers wrote no multiprocess files"

    samples = _collect(tmp_path)
    labels = (("endpoint", "/urls"), ("method", "GET"), ("status", "200"))
    assert samples[("http_requests_total", labels)] == 2.0
    err_labels = (("endpoint", "/urls"), ("method", "GET"), ("status", "500"))
    assert samples[("http_errors_total", err_labels)] == 2.0
    # livesum: total in flight across both workers.
    assert samples[("http_requests_in_progress", ())] == 2.0
    # max CPU / summed memory stay single-series (dashboard exprs unchanged).
    assert samples[("process_cpu_percent", ())] == 12.5
    assert samples[("process_memory_mb", ())] == 200.0


def test_gunicorn_on_starting_cleans_dir(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gunicorn_conf_under_test", str(REPO_ROOT / "gunicorn.conf.py")
    )
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)

    stale = tmp_path / "counter_123.db"
    stale.write_text("junk")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    conf.on_starting(MagicMock())
    assert list(tmp_path.iterdir()) == []


def test_gunicorn_child_exit_reaps_dead_worker(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gunicorn_conf_reap_test", str(REPO_ROOT / "gunicorn.conf.py")
    )
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    _run_worker(tmp_path)
    files_before = list(tmp_path.iterdir())
    assert files_before, "worker wrote no files"

    server = SimpleNamespace(log=MagicMock())
    # Multiprocess files are named <kind>_<mode>_<pid>.db; reap every pid.
    # Counters/histograms of dead workers persist by design (cumulative);
    # only live* gauge files must go.
    for pid in {int(p.name.split("_")[-1].split(".")[0]) for p in files_before}:
        conf.child_exit(server, SimpleNamespace(pid=pid))
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining, "cumulative counter files must persist"
    assert not [n for n in remaining if "_livesum_" in n or "_livemax_" in n], remaining
    server.log.exception.assert_not_called()
