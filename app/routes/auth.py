from flask import Blueprint, abort, g, jsonify

from app.models.user import User
from app.utils.auth import issue_api_key, require_auth
from app.utils.ratelimit import rate_limit
from app.utils.validation import require_json

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/auth/api-keys", methods=["POST"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def create_api_key():
    """Mint an additional bearer key for the authenticated user (#99)."""
    data = require_json("Invalid JSON received for create_api_key")
    name = data.get("name", "")
    if not isinstance(name, str):
        abort(400, description="name must be a string")

    try:
        owner = User.get_by_id(g.current_user_id)
    except User.DoesNotExist:
        abort(401, description="Missing or invalid API key")

    record, raw = issue_api_key(owner.id, name=name.strip())
    return (
        jsonify(
            {
                "id": record.id,
                "user_id": owner.id,
                "name": record.name,
                "api_key": raw,
            }
        ),
        201,
    )
