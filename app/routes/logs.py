from flask import Blueprint, jsonify

from app.log_store import log_records, log_records_lock
from app.utils.auth import require_admin
from app.utils.pagination import bounds, envelope, is_v1
from app.utils.ratelimit import rate_limit
from app.utils.schemas import ListQuery, parse_query

logs_bp = Blueprint("logs", __name__)


@logs_bp.route("/logs", methods=["GET"])
@require_admin
@rate_limit(capacity=60, refill_rate=1.0)
def get_logs():
    params = parse_query(ListQuery)
    offset, size = bounds(params)
    if params.before_id is not None:
        from flask import abort

        abort(400, description="Log snapshots support offset pagination only")
    with log_records_lock:
        recent = list(log_records)
    if not is_v1():
        return jsonify({"logs": recent[-50:]})
    # A bounded, newest-first snapshot, not a durable audit log/cursor.
    recent.reverse()
    items = recent[offset : offset + size]
    return jsonify(envelope(items, params, len(recent) > offset + size, {"logs": items}))
