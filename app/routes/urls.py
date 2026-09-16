import json
import secrets
import string
import uuid
from datetime import datetime, timezone

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
from app.utils.events import create_event
from app.utils.kafka_producer import publish_url_create
from app.utils.auth import require_auth, require_owner, scope_query, cache_scope, is_admin
from flask import g
from hashlib import sha256
from app.utils.ratelimit import rate_limit
from app.utils.validation import (
    parse_expires_at,
    reject_unknown_fields,
    require_bool,
    require_int,
    require_json,
    require_non_empty_str,
    require_str,
    validate_offset_params,
)


urls_bp = Blueprint("urls", __name__)

MAX_URL_LENGTH = 2048
# Postgres VARCHAR(255) storage limit for Url.original_url/Url.title (#238).
# Longer values raise DataError inside the sync fallback and surface as a
# misleading 500, so reject them early with a 400. URL semantics still cap at
# MAX_URL_LENGTH via is_valid_url; the DB cap (255) is enforced explicitly.
MAX_ORIGINAL_URL_LENGTH = 255
MAX_TITLE_LENGTH = 255


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
    data["expires_at"] = _expires_at_iso(data.get("expires_at"))
    return data


def _expires_at_iso(value):
    """Serialize ``expires_at`` as an ISO 8601 string or None (#192).

    Postgres ``TIMESTAMP`` strips tz on refetch, so naive datetimes are
    normalized to UTC (values are validated tz-aware at write time).
    Pre-serialized strings (e.g. cache round-trips) pass through.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


def _is_expired(value, now):
    """True when an ``expires_at`` value (datetime|str|None) is at/past ``now``.

    Naive datetimes/strings are treated as UTC rather than raising
    ``TypeError`` on aware/naive comparison: PG ``TIMESTAMP`` strips tz on
    refetch, so stored values routinely come back naive. Unparseable strings
    fail open (not expired) with a warning — a corrupt cache entry must not
    404 a live link.
    """
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except (ValueError, TypeError):
            current_app.logger.warning(f"Unparseable expires_at in cached URL: {value!r}")
            return False
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value <= now
    return False


def _idempotency_key(data):
    """Client-supplied idempotency key for POST /urls (#113).

    The ``Idempotency-Key`` header wins over the ``request_id`` JSON field;
    empty values are ignored (treated as absent).
    """
    header_key = request.headers.get("Idempotency-Key")
    if isinstance(header_key, str) and header_key.strip():
        return header_key.strip()
    body_key = data.get("request_id") if isinstance(data, dict) else None
    if isinstance(body_key, str) and body_key.strip():
        return body_key.strip()
    return None


@urls_bp.route("/urls", methods=["POST"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def create_url():
    data = require_json("Invalid JSON received for create_url")

    user_id = data.get("user_id", g.current_user_id)
    require_owner(user_id)
    original_url = data.get("original_url")
    title = data.get("title")

    require_int(user_id, "user_id must be an integer", "user_id must be an integer")

    require_str(original_url, "original_url must be a string", "original_url must be a string")

    if len(original_url) > MAX_URL_LENGTH:
        current_app.logger.warning(f"Rejected overlong original_url: length={len(original_url)}")
        abort(400, description="original_url must not exceed 2048 characters in length")

    if len(original_url) > MAX_ORIGINAL_URL_LENGTH:
        current_app.logger.warning(f"Rejected overlong original_url: length={len(original_url)}")
        abort(400, description="original_url must not exceed 255 characters in length")

    if not is_valid_url(original_url):
        current_app.logger.warning(f"Rejected unsafe original_url: {original_url[:80]}")
        abort(400, description="original_url must be a valid http(s) URL")

    require_str(title, "title must be a string", "title must be a string")

    if len(title) > MAX_TITLE_LENGTH:
        current_app.logger.warning(f"Rejected overlong title: length={len(title)}")
        abort(400, description="title must not exceed 255 characters in length")

    expires_at = parse_expires_at(data.get("expires_at"))

    if get_user(user_id) is None:
        current_app.logger.warning("User not found")
        abort(400, description="User not found")

    request_id = f"u{user_id}:{uuid.uuid4()}"

    idempotency_key = _idempotency_key(data)
    if idempotency_key is not None:
        # Namespace before both persistence and Kafka/status cache publication.
        # A fixed-length digest avoids DB key overflow and cross-user collisions.
        idempotency_key = f"u{user_id}:" + sha256(idempotency_key.encode()).hexdigest()
        existing = Url.get_or_none((Url.request_id == idempotency_key) & (Url.user == user_id))
        if existing is not None:
            # Client retry of an already-created URL: return the original
            # row without publishing again (#113).
            replay = format_url(existing)
            set_url(existing.id, replay)
            set_url_by_short_code(existing.short_code, replay)
            clear_list_cache("list:urls:")
            clear_list_cache("list:events:")
            current_app.logger.info(
                f"Idempotent replay: request_id={idempotency_key} url_id={existing.id}"
            )
            return jsonify(replay), 200
        request_id = idempotency_key

    try:
        created = publish_url_create(
            {
                "request_id": request_id,
                "user_id": user_id,
                "original_url": original_url,
                "title": title,
                "expires_at": expires_at.isoformat() if expires_at is not None else None,
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
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def get_url_status(request_id):
    from app.cache import get_l2

    # Older IDs have no owner marker: only a persisted owned row can authorize
    # them. Do not trust unowned Redis payloads, including pending/error states.
    if not request_id.startswith(f"u{g.current_user_id}:") and not is_admin():
        existing = Url.get_or_none((Url.request_id == request_id) & (Url.user == g.current_user_id))
        if existing is None:
            abort(404)

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
                "expires_at": status_data.get("expires_at"),
            }
        )

    return jsonify({"status": "pending"})


def _require_int_query_param(name):
    """Parse an integer query param or abort 400 (#242).

    ``request.args.get(name, type=int)`` yields ``None`` on garbage input,
    which would otherwise produce an ``IS NULL`` comparison and silently
    return an empty list. Treat present-but-unparseable as a client error,
    following the ``is_active`` 400 pattern.
    """
    value = request.args.get(name, type=int)
    if value is None:
        current_app.logger.warning(f"Invalid {name} query param: {request.args.get(name)!r}")
        abort(400, description=f"{name} must be an integer")
    return value


@urls_bp.route("/urls", methods=["GET"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def list_urls():
    offset = request.args.get("offset", 0, type=int)
    size = request.args.get("size", 20, type=int)
    offset, size = validate_offset_params(offset, size)

    # Canonical cache key from validated params only (#111): bounds key
    # cardinality instead of caching arbitrary raw query strings.
    key_parts = [f"offset={offset}", f"size={size}"]
    for name in ("id", "user_id", "short_code", "original_url", "is_active", "before_id"):
        if name in request.args:
            key_parts.append(f"{name}={request.args[name][:128]}")
    cache_key = "list:urls:" + cache_scope() + "&".join(key_parts)
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
        Url.expires_at,
        Url.created_at,
        Url.updated_at,
    )

    query = scope_query(query, Url.user)
    if "id" in request.args:
        query = query.where(Url.id == _require_int_query_param("id"))

    if "user_id" in request.args:
        query = query.where(Url.user_id == _require_int_query_param("user_id"))

    if "short_code" in request.args:
        query = query.where(Url.short_code == request.args["short_code"])

    if "original_url" in request.args:
        query = query.where(Url.original_url == request.args["original_url"])

    if "is_active" in request.args:
        val = request.args["is_active"].lower()
        if val not in ("true", "false"):
            current_app.logger.warning(
                f"Invalid is_active query param: {request.args['is_active']!r}"
            )
            abort(400, description="is_active must be 'true' or 'false'")
        query = query.where(Url.is_active == (val == "true"))

    if "before_id" in request.args:
        query = query.where(Url.id < _require_int_query_param("before_id"))
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
                "expires_at": _expires_at_iso(u.expires_at),
                "created_at": u.created_at.isoformat(),
                "updated_at": u.updated_at.isoformat(),
            }
            for u in urls
        ],
    }
    set_list_cache(cache_key, payload)
    return jsonify(payload)


@urls_bp.route("/urls/<int:url_id>", methods=["GET"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def get_url_cached(url_id):
    owner = Url.get_or_none(Url.id == url_id)
    if owner is None:
        abort(404)
    require_owner(owner.user_id)
    cached = get_url(url_id)
    if cached is not None:
        return jsonify(cached)
    try:
        url = Url.get_by_id(url_id)
        require_owner(url.user_id)
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
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def update_url(url_id):
    try:
        url = Url.get_by_id(url_id)
        require_owner(url.user_id)
    except Url.DoesNotExist:
        current_app.logger.warning(f"URL not found for update id={url_id}")
        abort(404)

    data = require_json("Invalid JSON received for update_url")

    reject_unknown_fields(data, {"title", "is_active", "expires_at"})

    if "title" in data:
        require_non_empty_str(data["title"], "title must be a non-empty string")
        if len(data["title"].strip()) > MAX_TITLE_LENGTH:
            current_app.logger.warning(
                f"Rejected overlong title on update: length={len(data['title'].strip())}"
            )
            abort(400, description="title must not exceed 255 characters in length")
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
        require_bool(data["is_active"], "is_active must be a boolean")
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

    if "expires_at" in data:
        expires_at = parse_expires_at(data["expires_at"])
        url.expires_at = expires_at
        create_event(
            url.id,
            url.user_id,
            "updated",
            {
                "field": "expires_at",
                "new_value": expires_at.isoformat() if expires_at is not None else None,
            },
        )
        current_app.logger.info(f"Updated expires_at for url id={url.id}")

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
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
def delete_url_endpoint(url_id):
    from app.database import db

    try:
        url = Url.get_by_id(url_id)
        require_owner(url.user_id)
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
    if _is_expired(data.get("expires_at"), datetime.now(timezone.utc)):
        current_app.logger.warning(f"Short code expired: {short_code}")
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
