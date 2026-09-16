import csv

from flask import Blueprint, abort, current_app, jsonify, request
from peewee import chunked
from playhouse.shortcuts import model_to_dict

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
from app.database import db
from app.models.url import Url
from app.models.user import User
from app.utils.auth import require_auth
from app.utils.ratelimit import rate_limit
from app.utils.validation import (
    reject_unknown_fields,
    require_json,
    require_non_empty_str,
    validate_page_params,
)

users_bp = Blueprint("users", __name__)


@users_bp.route("/users/bulk", methods=["POST"])
@rate_limit(capacity=10, refill_rate=1.0)
@require_auth
def bulk_import_users():
    if not request.content_type or not request.content_type.startswith("multipart/form-data"):
        current_app.logger.warning(f"Invalid Content-Type for bulk import: {request.content_type}")
        abort(415, description="Content-Type must be multipart/form-data")

    if "file" not in request.files:
        current_app.logger.warning("Missing 'file' field in bulk import request")
        abort(400, description="Missing 'file' field")

    file = request.files["file"]
    if not file.filename or not file.filename.endswith(".csv"):
        current_app.logger.warning("Invalid file type for bulk import")
        abort(400, description="Invalid file type, expected .csv")

    try:
        raw = file.stream.read()
        if len(raw) > 5 * 1024 * 1024:
            abort(400, description="CSV too large (max 5MB)")
        text = raw.decode("utf-8")
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
        # Tolerate unexpected columns by whitelisting (#130).
        rows.append({"username": username, "email": email})

    if not rows:
        return jsonify({"imported": 0}), 200

    # Non-destructive: insert new rows, skip existing usernames/emails (#107).
    # Never drop tables here — the old code deleted urls/events via cascade.
    imported = 0
    with db.atomic():
        for batch in chunked(rows, 100):
            inserted = User.insert_many(batch).on_conflict_ignore().as_rowcount().execute()
            imported += inserted

    clear_all_users()
    clear_list_cache("list:users:")

    return jsonify({"imported": imported}), 200


@users_bp.route("/users", methods=["GET"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def list_users():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    page, per_page = validate_page_params(page, per_page)

    cache_key = f"list:users:page={page}&per_page={per_page}"
    cached = get_list_cache(cache_key)
    if cached is not None:
        return jsonify(cached)

    offset = (page - 1) * per_page
    users = (
        User.select(User.id, User.username, User.email, User.created_at)
        .order_by(User.id)
        .limit(per_page)
        .offset(offset)
    )

    payload = {
        "kind": "list",
        "sample": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "created_at": u.created_at.isoformat(),
            }
            for u in users
        ],
    }
    set_list_cache(cache_key, payload)
    return jsonify(payload)


@users_bp.route("/users/<int:user_id>", methods=["GET"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def get_user_cached(user_id):
    cached = get_user(user_id)
    if cached is not None:
        return jsonify(cached)
    try:
        user = User.get_by_id(user_id)
    except User.DoesNotExist:
        abort(404)
    data = model_to_dict(user)
    set_user(user_id, data)
    return jsonify(data)


@users_bp.route("/users", methods=["POST"])
@rate_limit(capacity=300, refill_rate=5.0)
def create_user():
    data = require_json("Invalid JSON received for create_user")

    username = data.get("username")
    email = data.get("email")

    require_non_empty_str(username, "username must be a non-empty string")
    require_non_empty_str(email, "email must be a non-empty string")

    # Prevent mass assignment: only allow whitelisted fields (#103).
    reject_unknown_fields(data, {"username", "email"})

    try:
        user = User.create(username=username.strip(), email=email.strip())
    except Exception as e:
        current_app.logger.exception(f"Failed to create user: {e}")
        abort(400, description="Could not create user (duplicate?)")

    result = model_to_dict(user)
    set_user(user.id, result)
    clear_list_cache("list:users:")
    # Registration issues the caller's first bearer key inline (bootstrap:
    # there is no key to authenticate with yet). Shown once, never stored
    # raw (#99).
    from app.utils.auth import issue_api_key

    _, raw_key = issue_api_key(user.id)
    result["api_key"] = raw_key
    return jsonify(result), 201


@users_bp.route("/users/<int:user_id>", methods=["PUT"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def update_user(user_id):
    try:
        user = User.get_by_id(user_id)
    except User.DoesNotExist:
        abort(404)

    data = require_json("Invalid JSON received for update_user")

    reject_unknown_fields(data, {"username", "email"})

    if "username" in data:
        require_non_empty_str(data["username"], "username must be a non-empty string")
        user.username = data["username"].strip()
    if "email" in data:
        require_non_empty_str(data["email"], "email must be a non-empty string")
        user.email = data["email"].strip()

    try:
        user.save()
    except Exception:
        current_app.logger.exception(f"Failed to update user id={user_id}")
        abort(400, description="Could not update user (duplicate?)")
    data = model_to_dict(user)
    set_user(user_id, data)
    clear_list_cache("list:users:")
    return jsonify(data)


@users_bp.route("/users/<int:user_id>", methods=["DELETE"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def delete_user_endpoint(user_id):
    try:
        user = User.get_by_id(user_id)
        owned = list(Url.select(Url.id, Url.short_code).where(Url.user == user_id))
        short_codes = [u.short_code for u in owned if u.short_code]
        url_ids = [u.id for u in owned]
        with db.atomic():
            user.delete_instance(recursive=True)
        delete_user(user_id)
        for url_id in url_ids:
            delete_url(url_id)
        for short_code in short_codes:
            delete_url_by_short_code(short_code)
        clear_list_cache("list:users:")
        clear_list_cache("list:urls:")
        clear_list_cache("list:events:")
        current_app.logger.info(f"Deleted user id={user_id}")
    except User.DoesNotExist:
        current_app.logger.warning(f"User not found for delete id={user_id}")

    return jsonify({}), 200
