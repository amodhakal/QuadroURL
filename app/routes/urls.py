import random
import string
import uuid

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect as flask_redirect,
    request,
)
from playhouse.shortcuts import model_to_dict

from app.cache import (
    clear_list_cache,
    delete_url,
    get_list_cache,
    get_url,
    get_url_by_short_code,
    get_user,
    set_list_cache,
    set_url,
    set_url_by_short_code,
)
from app.models.url import Url
from app.utils.events import create_event_async as create_event
from app.utils.kafka_producer import publish_url_create


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

    request_id = str(uuid.uuid4())

    created = publish_url_create({
        "request_id": request_id,
        "user_id": user_id,
        "original_url": original_url,
        "title": title,
    })

    if created is not None:
        current_app.logger.info(
            f"Short URL created with id={created.get('id')} short_code={created.get('short_code')}"
        )
        clear_list_cache("list:urls:")
        clear_list_cache("list:events:")
        return jsonify(created), 201

    current_app.logger.info(
        f"URL create requested: request_id={request_id} user_id={user_id}"
    )

    return jsonify({
        "request_id": request_id,
        "status": "pending",
    }), 202


@urls_bp.route("/urls/<request_id>/status", methods=["GET"])
def get_url_status(request_id):
    from app.cache import get_l2

    try:
        r = get_l2()
        if r is None:
            abort(503, description="Status store unavailable")
        raw = r.get(f"url-pending:{request_id}")
    except Exception:
        abort(503, description="Status store unavailable")

    if raw is None:
        abort(404)

    status_data = json.loads(raw)

    if status_data.get("status") == "error":
        return jsonify({
            "status": "error",
            "error": status_data.get("error", "Unknown error"),
        }), 500

    if status_data.get("status") == "ready":
        return jsonify({
            "status": "ready",
            "id": status_data.get("id"),
            "short_code": status_data.get("short_code"),
            "original_url": status_data.get("original_url"),
            "title": status_data.get("title"),
        })

    return jsonify({"status": "pending"})


@urls_bp.route("/urls", methods=["GET"])
def list_urls():
    cache_key = f"list:urls:{request.query_string.decode()}"
    cached = get_list_cache(cache_key)
    if cached is not None:
        return jsonify(cached)

    offset = request.args.get("offset", 0, type=int)
    size = request.args.get("size", 20, type=int)

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

    if "before_id" in request.args:
        query = query.where(Url.id < request.args.get("before_id", type=int))
        query = query.order_by(Url.id.desc()).limit(size)
        urls = list(query)
    else:
        query = query.order_by(Url.id).limit(size).offset(offset)
        urls = list(query)

    current_app.logger.info(f"Listed {len(urls)} URL records")

    payload = {
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
    }
    set_list_cache(cache_key, payload)
    return jsonify(payload)


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
    clear_list_cache("list:urls:")
    clear_list_cache("list:events:")
    return jsonify(data)


@urls_bp.route("/urls/<int:url_id>", methods=["DELETE"])
def delete_url_endpoint(url_id):
    from app.database import db

    try:
        url = Url.get_by_id(url_id)
        with db.atomic():
            url.delete_instance(recursive=True)
        delete_url(url_id)
        clear_list_cache("list:urls:")
        clear_list_cache("list:events:")
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
