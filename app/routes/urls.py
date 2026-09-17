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
    get_user,
    set_list_cache,
    set_url,
    set_url_by_short_code,
)
from app.models.url import Url
from app.utils.events import create_event
from app.utils.kafka_producer import publish_url_create
from app.utils.auth import require_auth, require_owner, scope_query, is_admin
from flask import g
from hashlib import sha256
from app.utils.ratelimit import rate_limit
from app.utils.schemas import UrlCreate, UrlUpdate, UrlQuery, parse_body, parse_query
from app.utils.pagination import bounds, cache_key as list_cache_key, paginate, envelope


urls_bp = Blueprint("urls", __name__)

MAX_URL_LENGTH = 2048
# Postgres VARCHAR(255) storage limit for Url.original_url/Url.title (#238).
# Longer values raise DataError inside the sync fallback and surface as a
# misleading 500, so reject them early with a 400. URL semantics still cap at
# MAX_URL_LENGTH via is_valid_url; the DB cap (255) is enforced explicitly.
MAX_ORIGINAL_URL_LENGTH = 255
MAX_TITLE_LENGTH = 255


def is_valid_url(value: str) -> bool:
    """Compatibility URL predicate; request validation uses UrlCreate (#191)."""
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
@require_auth
@rate_limit(capacity=300, refill_rate=5.0)
def create_url():
    data = parse_body(UrlCreate)
    user_id = data.get("user_id", g.current_user_id)
    require_owner(user_id)
    original_url = data["original_url"]
    from app.utils.url_safety import require_destination_safe

    require_destination_safe(original_url)
    title = data["title"]
    expires_at = data.get("expires_at")

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
@rate_limit(capacity=300, refill_rate=5.0)
def get_url_status(request_id):
    from app.cache import get_l2

    # Older IDs have no owner marker: only a persisted owned row can authorize
    # them. Do not trust unowned Redis payloads, including pending/error states.
    existing = Url.get_or_none(Url.request_id == request_id)
    if existing is not None:
        require_owner(existing.user_id)
    elif not request_id.startswith(f"u{g.current_user_id}:") and not is_admin():
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
@rate_limit(capacity=300, refill_rate=5.0)
def list_urls():
    params = parse_query(UrlQuery)
    bounds(params)
    cache_key = list_cache_key("urls", params)
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
    for name in ("id", "user_id", "short_code", "original_url", "is_active"):
        value = getattr(params, name)
        if value is not None:
            query = query.where(getattr(Url, name) == value)
    urls, has_more = paginate(query, Url.id, params)

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
    payload = envelope(payload["sample"], params, has_more, payload)
    set_list_cache(cache_key, payload)
    return jsonify(payload)


@urls_bp.route("/urls/<int:url_id>", methods=["GET"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
@rate_limit(capacity=300, refill_rate=5.0)
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
@rate_limit(capacity=300, refill_rate=5.0)
def update_url(url_id):
    try:
        url = Url.get_by_id(url_id)
        require_owner(url.user_id)
    except Url.DoesNotExist:
        current_app.logger.warning(f"URL not found for update id={url_id}")
        abort(404)

    data = parse_body(UrlUpdate)

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

    if "expires_at" in data:
        expires_at = data["expires_at"]
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
@rate_limit(capacity=300, refill_rate=5.0)
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
    """Apply the same fresh password, lifecycle and safety policy on every alias."""
    from app.utils.link_access import resolve_public_link

    return resolve_public_link(short_code)


def track_click(data, short_code):
    """Best-effort click tracking: skips bots, never breaks redirects (#147).

    Click payloads carry referrer/user-agent/visitor data for #177 analytics;
    parsing failures degrade to "direct"/Unknown without breaking redirects.
    """
    try:
        user_agent = request.headers.get("User-Agent", "")
        referrer = request.headers.get("Referer", "") or ""
    except Exception:
        user_agent, referrer = "", ""
    if is_bot_user_agent(user_agent):
        return
    try:
        from app.routes.analytics import classify_user_agent

        create_event(
            data["id"],
            data["user_id"],
            "click",
            {
                "short_code": short_code,
                "referrer": referrer,
                "user_agent": classify_user_agent(user_agent),
                "visitor": stable_visitor_id(),
            },
        )
    except Exception:
        current_app.logger.exception("Click tracking failed (redirect unaffected)")


def stable_visitor_id():
    """Rotate daily, hash client traits; never store a raw IP address."""
    import hashlib
    from datetime import datetime, timezone

    from app.utils.request_ctx import get_client_ip

    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    traits = f"{get_client_ip()}|{request.headers.get('User-Agent', '')}"
    return f"{day}:{hashlib.sha256(traits.encode('utf-8')).hexdigest()[:32]}"


@urls_bp.route("/urls/<short_code>/redirect", methods=["GET", "POST"])
@rate_limit(capacity=2000, refill_rate=200.0)
def redirect_short_code(short_code):
    data = resolve_short_code_or_404(short_code)

    track_click(data, short_code)

    current_app.logger.info(f"Redirecting short code {short_code} to {data['original_url']}")
    return flask_redirect(data["original_url"])


@urls_bp.route("/r/<short_code>", methods=["GET", "POST"])
@rate_limit(capacity=2000, refill_rate=200.0)
def redirect_short_code_legacy(short_code):
    data = resolve_short_code_or_404(short_code)

    track_click(data, short_code)

    current_app.logger.info(f"Redirecting short code {short_code} to {data['original_url']}")
    return jsonify({"url": data["original_url"], "short_code": short_code})
