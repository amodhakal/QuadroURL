import os

import psutil
from flask import Blueprint, jsonify

from app.metrics_store import get_metrics_snapshot

metrics_bp = Blueprint("metrics", __name__)


@metrics_bp.route("/metrics", methods=["GET"])
def get_metrics():
    # System stats come from the background sampler gauges (#160) to keep
    # psutil syscalls off the request hot path. Fall back to a cheap
    # one-shot read only if the sampler has never run (e.g. in tests).
    try:
        from app.routes.prometheus import CPU_USAGE, MEMORY_USAGE_MB

        cpu_percent = float(CPU_USAGE._value.get())
        process_memory_mb = float(MEMORY_USAGE_MB._value.get())
        sampler_warmed = (cpu_percent != 0.0 or process_memory_mb != 0.0)
    except Exception:
        cpu_percent = 0.0
        process_memory_mb = 0.0
        sampler_warmed = False

    if not sampler_warmed:
        try:
            process = psutil.Process(os.getpid())
            process_memory_mb = round(process.memory_info().rss / 1024 / 1024, 1)
        except Exception:
            pass

    try:
        memory = psutil.virtual_memory()
        used_gb = round(memory.used / 1024 / 1024 / 1024, 1)
        total_gb = round(memory.total / 1024 / 1024 / 1024, 1)
    except Exception:
        used_gb = 0.0
        total_gb = 0.0

    snapshot = get_metrics_snapshot()

    return jsonify(
        {
            "system": {
                "cpu_percent": cpu_percent,
                "system_ram": {
                    "used_gb": used_gb,
                    "total_gb": total_gb,
                },
                "process_memory_mb": process_memory_mb,
            },
            "latency": snapshot["latency"],
            "traffic": snapshot["traffic"],
            "errors": snapshot["errors"],
            "saturation": snapshot["saturation"],
            "uptime_seconds": snapshot["uptime_seconds"],
            "recent_requests": snapshot["recent_requests"],
        }
    )
