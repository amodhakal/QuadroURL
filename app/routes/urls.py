import random
import string

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect as flask_redirect,
    request,
)
from peewee import IntegrityError
from playhouse.shortcuts import model_to_dict

from app.cache import (
    delete_url,
    get_url,
    get_url_by_short_code,
    get_user,
    set_url,
    set_url_by_short_code,
)
from app.models.url import Url
from app.models.user import User
from app.utils.events import create_event_async as create_event


urls_bp = Blueprint("urls", __name__)


def generate_short_code(length=6):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def format_url(url):
    data = model_to_dict(url, recurse=False)
    data["user_id"] = data.pop("user")
    return data


@urls_bp.route("/urls", methods=["POST"])
def create_url():
    data = request.get_json(silent=True)

    if not data:
        current_app.logger.warning("Invalid JSON received for create_url")
        abort(400, description="Invalid JSON")

    user_id = data.get("user_id")
    original_url = data.get("original_url")
    title = data.get("title")

    if not user_id or not isinstance(user_id, int):
        current_app.logger.warning("user_id must be an integer")
        abort(400, description="user_id must be an integer")

    if not original_url or not isinstance(original_url, str):
        current_app.logger.warning("original_url must be a string")
        abort(400, description="original_url must be a string")

    if not title or not isinstance(title, str):
        current_app.logger.warning("title must be a string")
        abort(400, description="title must be a string")

    if get_user(user_id) is None:
        current_app.logger.warning("User not found")
        abort(400, description="User not found")

    for _ in range(5):
        short_code = generate_short_code()
        try:
            url = Url.create(
                user_id=user_id,
                short_code=short_code,
                original_url=original_url,
                title=title,
                is_active=True,
            )
            break
        except IntegrityError:
            continue
    else:
        abort(500, description="Failed to generate unique short code")

    create_event(
        url.id,
        user_id,
        "created",
        {
            "short_code": short_code,
            "original_url": original_url,
        },
    )

    current_app.logger.info(
        f"Short URL created with id={url.id} short_code={short_code}"
    )

    data = format_url(url)
    set_url(url.id, data)
    set_url_by_short_code(short_code, data)
    return jsonify(data), 201


@urls_bp.route("/urls", methods=["GET"])
def list_urls():
    offset = request.args.get("offset", 0, type=int)
    size = request.args.get("size", 20, type=int)

    body = request.get_json(silent=True) or {}
    if "user_id" in body:
        query = Url.select(
            Url.id,
            Url.user,
            Url.short_code,
            Url.original_url,
            Url.title,
            Url.is_active,
            Url.created_at,
            Url.updated_at,
        ).where(Url.user_id == body["user_id"])
    else:
        query = Url.select(
            Url.id,
            Url.user,
            Url.short_code,
            Url.original_url,
            Url.title,
            Url.is_active,
            Url.created_at,
            Url.updated_at,
        )

    if "id" in request.args:
        query = query.where(Url.id == request.args.get("id", type=int))

    if "user_id" in request.args:
        query = query.where(Url.user_id == request.args.get("user_id", type=int))

    if "short_code" in request.args:
        query = query.where(Url.short_code == request.args["short_code"])

    if "original_url" in request.args:
        query = query.where(Url.original_url == request.args["original_url"])

    if "is_active" in request.args:
        val = request.args["is_active"].lower()
        query = query.where(Url.is_active == (val == "true"))

    total = query.count()
    urls = list(query.limit(size).offset(offset))
    current_app.logger.info(f"Listed {len(urls)} URL records")

    return jsonify(
        {
            "kind": "list",
            "sample": [
                {
                    "id": u.id,
                    "user_id": u.user_id,
                    "short_code": u.short_code,
                    "original_url": u.original_url,
                    "title": u.title,
                    "is_active": u.is_active,
                    "created_at": u.created_at.isoformat(),
                    "updated_at": u.updated_at.isoformat(),
                }
                for u in urls
            ],
            "total_items": total,
        }
    )


@urls_bp.route("/urls/<int:url_id>", methods=["GET"])
def get_url_cached(url_id):
    cached = get_url(url_id)
    if cached is not None:
        return jsonify(cached)
    try:
        url = Url.get_by_id(url_id)
    except Url.DoesNotExist:
        current_app.logger.warning(f"URL not found for id={url_id}")
        abort(404)
    except Exception as error:
        current_app.logger.exception(
            f"Unexpected error fetching URL id={url_id}: {error}"
        )
        abort(500, description="Internal server error")

    data = format_url(url)
    set_url(url_id, data)
    current_app.logger.info(f"Fetched URL id={url_id}")
    return jsonify(data)


@urls_bp.route("/urls/<int:url_id>", methods=["PUT"])
def update_url(url_id):
    try:
        url = Url.get_by_id(url_id)
    except Url.DoesNotExist:
        current_app.logger.warning(f"URL not found for update id={url_id}")
        abort(404)

    data = request.get_json(silent=True)

    if not data:
        current_app.logger.warning("Invalid JSON received for update_url")
        abort(400, description="Invalid JSON")

    if "title" in data:
        url.title = data["title"]
        create_event(
            url.id,
            url.user_id,
            "updated",
            {
                "field": "title",
                "new_value": data["title"],
            },
        )
        current_app.logger.info(f"Updated title for url id={url.id}")

    if "is_active" in data:
        url.is_active = data["is_active"]
        create_event(
            url.id,
            url.user_id,
            "updated",
            {
                "field": "is_active",
                "new_value": data["is_active"],
            },
        )
        current_app.logger.info(f"Updated is_active for url id={url.id}")

    url.save()
    data = format_url(url)
    set_url(url_id, data)
    return jsonify(data)


@urls_bp.route("/urls/<int:url_id>", methods=["DELETE"])
def delete_url(url_id):
    try:
        url = Url.get_by_id(url_id)
        url.delete_instance(recursive=True)
        delete_url(url_id)
        current_app.logger.info(f"Deleted URL id={url_id}")
    except Url.DoesNotExist:
        current_app.logger.warning(f"URL not found for delete id={url_id}")

    return jsonify({}), 200


@urls_bp.route("/urls/<short_code>/redirect", methods=["GET"])
def redirect_short_code(short_code):
    data = get_url_by_short_code(short_code)
    if data is None:
        current_app.logger.warning(f"Short code not found: {short_code}")
        abort(404)

    if not data.get("is_active", True):
        current_app.logger.warning(f"Short code inactive: {short_code}")
        abort(404)

    create_event(
        data["id"],
        data["user_id"],
        "click",
        {"short_code": short_code},
    )

    current_app.logger.info(
        f"Redirecting short code {short_code} to {data['original_url']}"
    )
    return flask_redirect(data["original_url"])


@urls_bp.route("/r/<short_code>", methods=["GET"])
def redirect_short_code_legacy(short_code):
    data = get_url_by_short_code(short_code)
    if data is None:
        current_app.logger.warning(f"Short code not found: {short_code}")
        abort(404)

    if not data.get("is_active", True):
        current_app.logger.warning(f"Short code inactive: {short_code}")
        abort(404)

    create_event(
        data["id"],
        data["user_id"],
        "click",
        {"short_code": short_code},
    )

    current_app.logger.info(
        f"Redirecting short code {short_code} to {data['original_url']}"
    )
    return jsonify({"url": data["original_url"], "short_code": short_code})
