from flask import Blueprint, jsonify

from app.log_store import log_records

logs_bp = Blueprint("logs", __name__)


@logs_bp.route("/logs", methods=["GET"])
def get_logs():
    # deque has no slicing — snapshot under lock for a consistent view.
    from app.log_store import log_records_lock

    with log_records_lock:
        recent = list(log_records)[-50:]
    return jsonify({"logs": recent})