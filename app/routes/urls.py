import json
import secrets
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
    delete_url_by_short_code,
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
from app.utils.ratelimit import rate_limit


urls_bp = Blueprint("urls", __name__)

MAX_URL_LENGTH = 2048


def is_valid_url(value: str) -> bool:
    """Allow only http/https URLs with a host (prevents javascript:/data: open redirects)."""
    from urllib.parse import urlparse

    if not value or len(value) > MAX_URL_LENGTH:
        return False
    try:
        parsed = urlparse(value.strip())
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    if any(c.isspace() for c in value):
        return False
    return True


def generate_short_code(length=6):
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))


_BOT_UA_RE = None


def _bot_ua_re():
    global _BOT_UA_RE
    if _BOT_UA_RE is None:
        import re as _re

        _BOT_UA_RE = _re.compile(
            r"bot|crawl|spider|slurp|mediapartners|baidu|yandex|sogou|exabot|facebot|ia_archiver"
            r"|prometheus|kube-probe|health-check|healthcheck|uptime|pingdom|datadog|newrelic",
            _re.IGNORECASE,
        )
    return _BOT_UA_RE


def is_bot_user_agent(user_agent: str) -> bool:
    """True for crawlers/monitors whose clicks shouldn't count as engagement."""
    if not user_agent:
        return False
    return bool(_bot_ua_re().search(user_agent))


def format_url(url):
    data = model_to_dict(url, recurse=False)
    data["user_id"] = data.pop("user")
    return data


@urls_bp.route("/urls", methods=["POST"])
@rate_limit(capacity=300, refill_rate=5.0)
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

    if not is_valid_url(original_url):
        current_app.logger.warning(f"Rejected unsafe original_url: {original_url[:80]}")
        abort(400, description="original_url must be a valid http(s) URL")

    if not title or not isinstance(title, str):
        current_app.logger.warning("title must be a string")
        abort(400, description="title must be a string")

    if get_user(user_id) is None:
        current_app.logger.warning("User not found")
        abort(400, description="User not found")

    request_id = str(uuid.uuid4())

    try:
        created = publish_url_create(
            {
                "request_id": request_id,
                "user_id": user_id,
                "original_url": original_url,
                "title": title,
            }
        )
    except RuntimeError:
        # Short-code retries exhausted (sync fallback). Static message —
        # safe to surface via the 500 handler's intentional-message path.
        current_app.logger.exception("Short-code generation exhausted")
        abort(500, description="Failed to generate unique short code")

    if created is not None:
        current_app.logger.info(
            f"Short URL created with id={created.get('id')} short_code={created.get('short_code')}"
        )
        clear_list_cache("list:urls:")
        clear_list_cache("list:events:")
        return jsonify(created), 201

    current_app.logger.info(f"URL create requested: request_id={request_id} user_id={user_id}")

    return jsonify(
        {
            "request_id": request_id,
            "status": "pending",
        }
    ), 202


@urls_bp.route("/urls/<request_id>/status", methods=["GET"])
def get_url_status(request_id):
    from app.cache import get_l2

    try:
        r = get_l2()
        if r is None:
            abort(503, description="Status store unavailable")
        raw = r.get(f"url-pending:{request_id}")
    except Exception as exc:
        # Don't swallow HTTPExceptions raised by abort() above.
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            raise
        current_app.logger.warning(f"Status store error: {exc}")
        abort(503, description="Status store unavailable")

    if raw is None:
        abort(404)

    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            current_app.logger.warning("Corrupted status payload (non-utf8)")
            abort(500, description="Corrupted status payload")

    try:
        status_data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        current_app.logger.warning("Corrupted status payload (invalid JSON)")
        abort(500, description="Corrupted status payload")

    if not isinstance(status_data, dict):
        current_app.logger.warning("Corrupted status payload (not an object)")
        abort(500, description="Corrupted status payload")

    if status_data.get("status") == "error":
        return jsonify(
            {
                "status": "error",
                "error": status_data.get("error", "Unknown error"),
            }
        ), 500

    if status_data.get("status") == "ready":
        return jsonify(
            {
                "status": "ready",
                "id": status_data.get("id"),
                "short_code": status_data.get("short_code"),
                "original_url": status_data.get("original_url"),
                "title": status_data.get("title"),
            }
        )

    return jsonify({"status": "pending"})


@urls_bp.route("/urls", methods=["GET"])
def list_urls():
    offset = request.args.get("offset", 0, type=int)
    size = request.args.get("size", 20, type=int)
    if offset is None or offset < 0:
        abort(400, description="offset must be >= 0")
    if size is None or size < 1 or size > 100:
        abort(400, description="size must be between 1 and 100")

    # Canonical cache key from validated params only (#111): bounds key
    # cardinality instead of caching arbitrary raw query strings.
    key_parts = [f"offset={offset}", f"size={size}"]
    for name in ("id", "user_id", "short_code", "original_url", "is_active", "before_id"):
        if name in request.args:
            key_parts.append(f"{name}={request.args[name][:128]}")
    cache_key = "list:urls:" + "&".join(key_parts)
    cached = get_list_cache(cache_key)
    if cached is not None:
        return jsonify(cached)

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
        if val not in ("true", "false"):
            abort(400, description="is_active must be 'true' or 'false'")
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
        current_app.logger.exception(f"Unexpected error fetching URL id={url_id}: {error}")
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

    allowed = {"title", "is_active"}
    unknown = set(data) - allowed
    if unknown:
        abort(400, description=f"Unknown fields: {sorted(unknown)}")

    if "title" in data:
        if not isinstance(data["title"], str) or not data["title"].strip():
            abort(400, description="title must be a non-empty string")
        url.title = data["title"].strip()
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
        if not isinstance(data["is_active"], bool):
            abort(400, description="is_active must be a boolean")
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
    # Keep the short-code cache coherent: redirects read via short_code,
    # so a stale entry would keep serving old title/is_active.
    set_url_by_short_code(url.short_code, data)
    clear_list_cache("list:urls:")
    clear_list_cache("list:events:")
    return jsonify(data)


@urls_bp.route("/urls/<int:url_id>", methods=["DELETE"])
def delete_url_endpoint(url_id):
    from app.database import db

    try:
        url = Url.get_by_id(url_id)
        short_code = url.short_code
        with db.atomic():
            url.delete_instance(recursive=True)
        delete_url(url_id)
        if short_code:
            delete_url_by_short_code(short_code)
        clear_list_cache("list:urls:")
        clear_list_cache("list:events:")
        current_app.logger.info(f"Deleted URL id={url_id}")
    except Url.DoesNotExist:
        current_app.logger.warning(f"URL not found for delete id={url_id}")

    return jsonify({}), 200


def resolve_short_code_or_404(short_code):
    """Shared lookup for both redirect routes (#190).

    Returns the cached URL dict, aborting 404 when missing/inactive.
    """
    data = get_url_by_short_code(short_code)
    if data is None:
        current_app.logger.warning(f"Short code not found: {short_code}")
        abort(404)
    if not data.get("is_active", True):
        current_app.logger.warning(f"Short code inactive: {short_code}")
        abort(404)
    return data


def track_click(data, short_code):
    """Best-effort click tracking: skips bots, never breaks redirects (#147)."""
    try:
        user_agent = request.headers.get("User-Agent", "")
    except Exception:
        user_agent = ""
    if is_bot_user_agent(user_agent):
        return
    try:
        create_event(
            data["id"],
            data["user_id"],
            "click",
            {"short_code": short_code},
        )
    except Exception:
        current_app.logger.exception("Click tracking failed (redirect unaffected)")


@urls_bp.route("/urls/<short_code>/redirect", methods=["GET"])
@rate_limit(capacity=2000, refill_rate=200.0)
def redirect_short_code(short_code):
    data = resolve_short_code_or_404(short_code)

    track_click(data, short_code)

    current_app.logger.info(f"Redirecting short code {short_code} to {data['original_url']}")
    return flask_redirect(data["original_url"])


@urls_bp.route("/r/<short_code>", methods=["GET"])
@rate_limit(capacity=2000, refill_rate=200.0)
def redirect_short_code_legacy(short_code):
    data = resolve_short_code_or_404(short_code)

    track_click(data, short_code)

    current_app.logger.info(f"Redirecting short code {short_code} to {data['original_url']}")
    return jsonify({"url": data["original_url"], "short_code": short_code})
