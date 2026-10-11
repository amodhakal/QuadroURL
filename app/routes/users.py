import csv

from flask import Blueprint, abort, current_app, jsonify, request
from peewee import DatabaseError, IntegrityError, InterfaceError, OperationalError
from playhouse.shortcuts import model_to_dict
from werkzeug.exceptions import HTTPException

from app.cache import (
    clear_all_users,
    clear_list_cache,
    delete_url,
    delete_url_by_short_code,
    delete_user,
    get_list_cache,
    get_user,
    set_list_cache,
    set_user,
)
from app.models.url import Url
from app.models.user import User
from app.utils.auth import (
    ActiveApiKeyLimitReached,
    issue_api_key,
    no_store,
    require_admin,
    require_auth,
    require_owner,
    scope_query,
)
from app.utils.ratelimit import rate_limit
from app.utils.schemas import ListQuery, UserCreate, UserUpdate, parse_body, parse_query, validate
from app.utils.pagination import bounds, cache_key as list_cache_key, envelope, paginate
from app.utils.serialization import iso_datetime, serialize_fields

users_bp = Blueprint("users", __name__)


def _user_dict(user):
    data = model_to_dict(user, exclude={"is_admin"})
    return serialize_fields(data, "created_at")


def _duplicate_user_error(error):
    text = str(error).lower()
    return isinstance(error, IntegrityError) and any(
        marker in text for marker in ("unique", "duplicate", "username", "email")
    )


def _abort_user_write(error, action):
    current_app.logger.exception("User %s failed", action)
    if isinstance(error, ActiveApiKeyLimitReached):
        abort(409, description="Active API key limit reached")
    if _duplicate_user_error(error):
        abort(400, description="A user with that username or email already exists")
    if isinstance(error, (OperationalError, InterfaceError, DatabaseError)):
        abort(503, description="User storage unavailable")
    abort(500, description="Internal server error")


def _existing_user(username, email):
    return User.get_or_none((User.username == username) | (User.email == email))


@users_bp.route("/users/bulk", methods=["POST"])
@rate_limit(capacity=10, refill_rate=1.0)
@require_admin
def bulk_import_users():
    if not request.content_type or not request.content_type.startswith("multipart/form-data"):
        current_app.logger.warning("Invalid Content-Type for bulk import: %s", request.content_type)
        abort(415, description="Content-Type must be multipart/form-data")

    if "file" not in request.files:
        current_app.logger.warning("Missing 'file' field in bulk import request")
        abort(400, description="Missing 'file' field")

    file = request.files["file"]
    if not file.filename or not file.filename.endswith(".csv"):
        current_app.logger.warning("Invalid file type for bulk import")
        abort(400, description="Invalid file type, expected .csv")

    try:
        raw = file.stream.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            abort(400, description="CSV too large (max 5MB)")
        text = raw.decode("utf-8")
    except HTTPException:
        raise
    except Exception:
        abort(400, description="Could not read CSV file")

    reader = csv.DictReader(text.splitlines())
    if (
        not reader.fieldnames
        or "username" not in reader.fieldnames
        or "email" not in reader.fieldnames
    ):
        abort(400, description="CSV must include username and email columns")

    rows = []
    for i, row in enumerate(reader, start=1):
        if len(rows) >= 5000:
            abort(400, description="CSV too many rows (max 5000)")
        username = (row.get("username") or "").strip()
        email = (row.get("email") or "").strip()
        if not username or not email:
            abort(400, description=f"Row {i}: username and email are required")
        rows.append(validate(UserCreate, {"username": username, "email": email}).model_dump())

    if not rows:
        return no_store(jsonify({"imported": 0, "credentials": []})), 200

    imported = 0
    credentials = []
    try:
        with User._meta.database.atomic():
            for row in rows:
                username = row["username"]
                email = row["email"]
                if _existing_user(username, email) is not None:
                    continue
                try:
                    with User._meta.database.atomic():
                        user = User.create(username=username, email=email)
                        _, raw_key = issue_api_key(user.id, enforce_limit=True)
                except IntegrityError:
                    if _existing_user(username, email) is None:
                        raise
                    continue
                except ActiveApiKeyLimitReached as exc:
                    raise exc
                imported += 1
                credentials.append(
                    {
                        "user_id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "api_key": raw_key,
                    }
                )
    except Exception as exc:
        _abort_user_write(exc, "bulk import")

    clear_all_users()
    clear_list_cache("list:users:")
    return no_store(jsonify({"imported": imported, "credentials": credentials})), 200


@users_bp.route("/users", methods=["GET"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def list_users():
    params = parse_query(ListQuery)
    bounds(params)
    cache_key = list_cache_key("users", params)
    cached = get_list_cache(cache_key)
    if cached is not None:
        return jsonify(cached)

    query = scope_query(User.select(User.id, User.username, User.email, User.created_at), User.id)
    users, has_more = paginate(query, User.id, params)

    payload = {
        "kind": "list",
        "sample": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "created_at": iso_datetime(u.created_at),
            }
            for u in users
        ],
    }
    payload = envelope(payload["sample"], params, has_more, payload)
    set_list_cache(cache_key, payload)
    return jsonify(payload)


@users_bp.route("/users/<int:user_id>", methods=["GET"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def get_user_cached(user_id):
    require_owner(user_id)
    cached = get_user(user_id)
    if cached is not None:
        if isinstance(cached, dict):
            cached = serialize_fields(cached, "created_at")
        return jsonify(cached)
    try:
        user = User.get_by_id(user_id)
    except User.DoesNotExist:
        abort(404)
    data = _user_dict(user)
    set_user(user_id, data)
    return jsonify(data)


@users_bp.route("/users", methods=["POST"])
def create_user():
    data = parse_body(UserCreate)
    username = data["username"]
    email = data["email"]

    try:
        with User._meta.database.atomic():
            user = User.create(username=username, email=email)
            _, raw_key = issue_api_key(user.id, enforce_limit=True)
    except Exception as exc:
        _abort_user_write(exc, "creation")

    result = _user_dict(user)
    set_user(user.id, result)
    clear_list_cache("list:users:")
    result = {**result, "api_key": raw_key}
    return no_store(jsonify(result)), 201


@users_bp.route("/users/<int:user_id>", methods=["PUT"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def update_user(user_id):
    require_owner(user_id)
    try:
        user = User.get_by_id(user_id)
    except User.DoesNotExist:
        abort(404)

    data = parse_body(UserUpdate)
    if not data:
        abort(400, description="At least one field must be provided")
    for field, value in data.items():
        setattr(user, field, value)

    try:
        user.save()
    except Exception as exc:
        _abort_user_write(exc, "update")
    data = _user_dict(user)
    set_user(user_id, data)
    clear_list_cache("list:users:")
    return jsonify(data)


@users_bp.route("/users/<int:user_id>", methods=["DELETE"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def delete_user_endpoint(user_id):
    require_owner(user_id)
    try:
        user = User.get_by_id(user_id)
        owned = list(Url.select(Url.id, Url.short_code).where(Url.user == user_id))
        short_codes = [u.short_code for u in owned if u.short_code]
        url_ids = [u.id for u in owned]
        with User._meta.database.atomic():
            user.delete_instance(recursive=True)
        delete_user(user_id)
        for url_id in url_ids:
            delete_url(url_id)
        for short_code in short_codes:
            delete_url_by_short_code(short_code)
        clear_list_cache("list:users:")
        clear_list_cache("list:urls:")
        clear_list_cache("list:events:")
        current_app.logger.info("Deleted user id=%s", user_id)
    except User.DoesNotExist:
        current_app.logger.warning("User not found for delete id=%s", user_id)
        abort(404, description="Not found")

    return jsonify({}), 200
