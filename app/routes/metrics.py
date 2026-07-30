import os

import psutil
from flask import Blueprint, jsonify

from app.metrics_store import get_metrics_snapshot

metrics_bp = Blueprint("metrics", __name__)


@metrics_bp.route("/metrics", methods=["GET"])
def get_metrics():
    process = psutil.Process(os.getpid())

    memory = psutil.virtual_memory()
    used_gb = round(memory.used / 1024 / 1024 / 1024, 1)
    total_gb = round(memory.total / 1024 / 1024 / 1024, 1)

    cpu_percent = psutil.cpu_percent(interval=None)
    process_memory_mb = round(process.memory_info().rss / 1024 / 1024, 1)

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
