import os
import secrets
from datetime import datetime, timezone

from flask import Blueprint, abort, g, jsonify, request, session
from peewee import IntegrityError
from werkzeug.exceptions import HTTPException

from app.models.api_key import ApiKey
from app.models.user import User
from app.utils.auth import (
    ActiveApiKeyLimitReached,
    authenticate_bearer,
    create_session_for_key,
    clear_auth_session,
    is_expired,
    issue_api_key,
    no_store,
    require_admin,
    require_auth,
)
from app.utils.ratelimit import rate_limit
from app.utils.schemas import AdminBootstrap, ApiKeyCreate, RoleUpdate, parse_body, validate
from app.utils.serialization import iso_datetime

auth_bp = Blueprint("auth", __name__)


@auth_bp.after_app_request
def protect_auth_responses(response):
    if request.blueprint in {"auth", "auth_v1"}:
        return no_store(response)
    return response


def _key_dict(record, now=None):
    active = record.revoked_at is None and not is_expired(record.expires_at, now)
    return {
        "id": record.id,
        "user_id": record.user_id,
        "name": record.name,
        "created_at": iso_datetime(record.created_at),
        "expires_at": iso_datetime(record.expires_at),
        "revoked_at": iso_datetime(record.revoked_at),
        "last_used_at": iso_datetime(record.last_used_at),
        "active": active,
        "is_active": active,
    }


def _user_dict(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_admin": bool(user.is_admin),
    }


def _owned_key(key_id, user_id):
    record = ApiKey.get_or_none((ApiKey.id == key_id) & (ApiKey.user == user_id))
    if record is None:
        abort(404)
    return record


def _raw_from_request():
    header = request.headers.get("Authorization", "")
    scheme, separator, raw = header.partition(" ")
    if separator and scheme.lower() == "bearer" and raw.strip():
        return raw.strip()
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        value = data.get("api_key")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _bootstrap_token(data):
    for name in ("X-Admin-Bootstrap-Token", "X-Bootstrap-Token", "X-Admin-Token"):
        value = request.headers.get(name)
        if value:
            return value
    for name in ("bootstrap_token", "token"):
        value = data.get(name)
        if isinstance(value, str) and value:
            return value
    return ""


def _bootstrap_identity(data):
    if data.get("user_id") is not None:
        return data
    if data.get("username") is not None and data.get("email") is not None:
        return data
    configured_id = os.environ.get("ADMIN_BOOTSTRAP_USER_ID")
    if configured_id:
        data = dict(data)
        data["user_id"] = configured_id
    return data


@auth_bp.route("/auth/api-keys", methods=["GET"])
@require_auth
@rate_limit(capacity=60, refill_rate=2.0)
def list_api_keys():
    records = list(
        ApiKey.select().where(ApiKey.user == g.current_user_id).order_by(ApiKey.id).limit(100)
    )
    return no_store(jsonify([_key_dict(record) for record in records]))


@auth_bp.route("/auth/api-keys", methods=["POST"])
@require_auth
@rate_limit(capacity=20, refill_rate=0.2)
def create_api_key():
    data = parse_body(ApiKeyCreate)
    try:
        owner = User.get_by_id(g.current_user_id)
    except User.DoesNotExist:
        abort(401, description="Missing or invalid API key")
    try:
        record, raw = issue_api_key(
            owner.id,
            name=data.get("name", ""),
            expires_at=data.get("expires_at"),
        )
    except ActiveApiKeyLimitReached:
        abort(409, description="Active API key limit reached")
    except ValueError:
        abort(400, description="Invalid API key expiry")
    return no_store(
        jsonify(
            {
                **_key_dict(record),
                "api_key": raw,
            }
        )
    ), 201


def _revoke_key(key_id):
    record = _owned_key(key_id, g.current_user_id)
    if record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
        record.save()
    return no_store(jsonify(_key_dict(record)))


@auth_bp.route("/auth/api-keys/<int:key_id>", methods=["DELETE"])
@require_auth
@rate_limit(capacity=60, refill_rate=2.0)
def revoke_api_key(key_id):
    return _revoke_key(key_id)


@auth_bp.route("/auth/api-keys/<int:key_id>/revoke", methods=["POST"])
@require_auth
@rate_limit(capacity=60, refill_rate=2.0)
def revoke_api_key_action(key_id):
    return _revoke_key(key_id)


def _revoke_all_keys():
    now = datetime.now(timezone.utc)
    changed = (
        ApiKey.update(revoked_at=now)
        .where((ApiKey.user == g.current_user_id) & ApiKey.revoked_at.is_null(True))
        .execute()
    )
    return no_store(jsonify({"revoked": int(changed)}))


@auth_bp.route("/auth/api-keys", methods=["DELETE"])
@require_auth
@rate_limit(capacity=10, refill_rate=0.2)
def revoke_all_api_keys():
    return _revoke_all_keys()


@auth_bp.route("/auth/api-keys/revoke-all", methods=["POST", "DELETE"])
@require_auth
@rate_limit(capacity=10, refill_rate=0.2)
def revoke_all_api_keys_action():
    return _revoke_all_keys()


@auth_bp.route("/auth/session", methods=["POST"])
@rate_limit(capacity=10, refill_rate=0.2)
def exchange_session():
    raw = _raw_from_request()
    record = authenticate_bearer(raw) if raw else None
    if record is None:
        abort(401, description="Missing or invalid API key")
    csrf_token = create_session_for_key(record)
    user = User.get_by_id(record.user_id)
    return no_store(
        jsonify(
            {
                "user": _user_dict(user),
                "user_id": record.user_id,
                "csrf_token": csrf_token,
            }
        )
    )


@auth_bp.route("/auth/session", methods=["GET"])
@auth_bp.route("/auth/current-session", methods=["GET"])
@require_auth
@rate_limit(capacity=60, refill_rate=2.0)
def current_session():
    user = User.get_by_id(g.current_user_id)
    csrf_token = session.get("csrf_token") if g.auth_method == "session" else None
    return no_store(
        jsonify(
            {
                "user": _user_dict(user),
                "user_id": user.id,
                "csrf_token": csrf_token,
                "auth_method": g.auth_method,
            }
        )
    )


@auth_bp.route("/auth/session", methods=["DELETE"])
@auth_bp.route("/auth/logout", methods=["POST", "DELETE"])
@require_auth
@rate_limit(capacity=20, refill_rate=0.5)
def logout():
    clear_auth_session()
    return no_store(jsonify({})), 200


@auth_bp.route("/auth/admin/bootstrap", methods=["POST"])
@auth_bp.route("/auth/bootstrap-admin", methods=["POST"])
@auth_bp.route("/auth/bootstrap", methods=["POST"])
@rate_limit(capacity=5, refill_rate=0.1)
def bootstrap_admin():
    configured = os.environ.get("ADMIN_BOOTSTRAP_TOKEN", "")
    if not configured:
        abort(404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    data = _bootstrap_identity(dict(data))
    provided = _bootstrap_token(data)
    if not provided or not secrets.compare_digest(
        provided.encode("utf-8"), configured.encode("utf-8")
    ):
        abort(401, description="Invalid bootstrap token")
    identity_data = {
        key: value for key, value in data.items() if key not in {"bootstrap_token", "token"}
    }
    try:
        identity = validate(AdminBootstrap, identity_data).model_dump(exclude_unset=True)
    except HTTPException:
        raise
    except Exception:
        abort(400, description="Invalid bootstrap request")
    if not identity:
        abort(400, description="Provide user_id or both username and email")

    try:
        with User._meta.database.atomic():
            if User.select(User.id).where(User.is_admin).exists():
                abort(409, description="An administrator already exists")
            if identity.get("user_id") is not None:
                user = User.get_by_id(identity["user_id"])
            else:
                user = User.create(
                    username=identity["username"],
                    email=identity["email"],
                )
            user.is_admin = True
            user.save()
            _, raw_key = issue_api_key(user.id, enforce_limit=True)
    except HTTPException:
        raise
    except ActiveApiKeyLimitReached:
        abort(409, description="Active API key limit reached")
    except User.DoesNotExist:
        abort(404, description="User not found")
    except IntegrityError:
        abort(409, description="User could not be created")
    except Exception:
        abort(503, description="Bootstrap storage unavailable")

    return no_store(jsonify({**_user_dict(user), "api_key": raw_key})), 201


def _set_role(user_id):
    data = parse_body(RoleUpdate)
    try:
        target = User.get_by_id(user_id)
    except User.DoesNotExist:
        abort(404)
    requested = data.get("is_admin")
    if target.is_admin and not requested:
        remaining = User.select(User.id).where(User.is_admin & (User.id != user_id)).exists()
        if not remaining:
            abort(409, description="Cannot remove the last administrator")
    target.is_admin = bool(requested)
    target.save()
    from app.cache import clear_list_cache, delete_user

    delete_user(user_id)
    clear_list_cache("list:users:")
    result = _user_dict(target)
    result["role"] = "admin" if target.is_admin else "user"
    return jsonify(result)


@auth_bp.route("/admin/users/<int:user_id>/role", methods=["PATCH", "PUT", "POST"])
@auth_bp.route("/users/<int:user_id>/role", methods=["PATCH", "PUT"])
@require_admin
@rate_limit(capacity=30, refill_rate=1.0)
def manage_role(user_id):
    return _set_role(user_id)
