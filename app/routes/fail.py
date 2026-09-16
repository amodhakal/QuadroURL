import os

from flask import Blueprint, abort, current_app, request

fail_bp = Blueprint("fail", __name__)


@fail_bp.route("/fail", methods=["GET"])
def fail():
    """Chaos kill-switch. Disabled unless explicitly enabled.

    Enable only in chaos-test environments via CHAOS_ENABLED=1, and
    require CHAOS_TOKEN when set. Otherwise any client could kill
    app replicas at will (#100).
    """
    if os.environ.get("CHAOS_ENABLED", "false").lower() != "true":
        abort(404)

    expected = os.environ.get("CHAOS_TOKEN", "")
    if expected:
        provided = request.headers.get("X-Chaos-Token", "")
        if not provided or provided != expected:
            abort(403, description="Forbidden")

    current_app.logger.warning("Chaos kill-switch triggered via /fail")
    os._exit(1)
