from flask import Blueprint, abort, g, jsonify

from app.models.user import User
from app.utils.auth import issue_api_key, require_auth
from app.utils.ratelimit import rate_limit
from app.utils.schemas import ApiKeyCreate, parse_body

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/auth/api-keys", methods=["POST"])
@require_auth
@rate_limit(capacity=20, refill_rate=0.2)
def create_api_key():
    """Mint an additional bearer key for the authenticated user (#99)."""
    data = parse_body(ApiKeyCreate)
    name = data.get("name", "")

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
