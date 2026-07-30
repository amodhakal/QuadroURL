import csv
import os

from flask import Blueprint, abort, current_app, jsonify, request
from peewee import chunked
from playhouse.shortcuts import model_to_dict

from app.cache import clear_all_users, delete_user, get_user, set_user
from app.database import db
from app.models.user import User

users_bp = Blueprint("users", __name__)

DATA_DIR = os.path.join("./data")


@users_bp.route("/users/bulk", methods=["POST"])
def bulk_import_users():
    if not request.content_type or not request.content_type.startswith(
        "multipart/form-data"
    ):
        current_app.logger.warning(
            f"Invalid Content-Type for bulk import: {request.content_type}"
        )
        abort(415, description="Content-Type must be multipart/form-data")

    if "file" not in request.files:
        current_app.logger.warning("Missing 'file' field in bulk import request")
        abort(400, description="Missing 'file' field")

    file = request.files["file"]
    if not file.filename or not file.filename.endswith(".csv"):
        current_app.logger.warning("Invalid file type for bulk import")
        abort(400, description="Invalid file type, expected .csv")

    reader = csv.DictReader(file.stream.read().decode("utf-8").splitlines())
    rows = [{k: v for k, v in row.items() if k != "id"} for row in reader]

    db.drop_tables([User], cascade=True)
    db.create_tables([User])

    with db.atomic():
        for batch in chunked(rows, 100):
            User.insert_many(batch).execute()

    clear_all_users()

    return jsonify({"imported": len(rows)}), 200


@users_bp.route("/users", methods=["GET"])
def list_users():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    offset = (page - 1) * per_page
    users = (
        User.select(User.id, User.username, User.email, User.created_at)
        .limit(per_page)
        .offset(offset)
    )

    return jsonify(
        {
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
    )


@users_bp.route("/users/<int:user_id>", methods=["GET"])
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
def create_user():
    data = request.get_json(silent=True)
    if not data:
        current_app.logger.warning("Invalid JSON received for create_user")
        abort(400, description="Invalid JSON")

    username = data.get("username")
    email = data.get("email")

    if not username or not isinstance(username, str) or not username.strip():
        abort(400, description="username must be a non-empty string")
    if not email or not isinstance(email, str) or not email.strip():
        abort(400, description="email must be a non-empty string")

    try:
        user = User.create(**data)
    except Exception as e:
        current_app.logger.exception(f"Failed to create user: {e}")
        abort(400, description=str(e))

    result = model_to_dict(user)
    set_user(user.id, result)
    return jsonify(result), 201


@users_bp.route("/users/<int:user_id>", methods=["PUT"])
def update_user(user_id):
    try:
        user = User.get_by_id(user_id)
    except User.DoesNotExist:
        abort(404)

    data = request.get_json(silent=True)
    if not data:
        current_app.logger.warning("Invalid JSON received for update_user")
        abort(400, description="Invalid JSON")

    if "username" in data:
        user.username = data["username"]
    if "email" in data:
        user.email = data["email"]

    user.save()
    data = model_to_dict(user)
    set_user(user_id, data)
    return jsonify(data)


@users_bp.route("/users/<int:user_id>", methods=["DELETE"])
def delete_user_endpoint(user_id):
    try:
        user = User.get_by_id(user_id)
        user.delete_instance()
        delete_user(user_id)
        current_app.logger.info(f"Deleted user id={user_id}")
    except User.DoesNotExist:
        current_app.logger.warning(f"User not found for delete id={user_id}")

    return jsonify({}), 200
