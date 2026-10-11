import functools
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from flask import abort, current_app, g, has_app_context, has_request_context, request, session
from werkzeug.exceptions import HTTPException


DEFAULT_LAST_USED_THROTTLE_SECONDS = 60
DEFAULT_MAX_ACTIVE_API_KEYS = 10
DEFAULT_SESSION_LIFETIME_HOURS = 8
MAX_ACTIVE_API_KEYS = DEFAULT_MAX_ACTIVE_API_KEYS
API_KEY_LAST_USED_THROTTLE_SECONDS = DEFAULT_LAST_USED_THROTTLE_SECONDS
KEY_PREFIX_LENGTH = 8


class ActiveApiKeyLimitReached(Exception):
    pass


def _config_int(names, default, minimum=1, maximum=2**31 - 1):
    if has_app_context():
        for name in names:
            value = current_app.config.get(name)
            if value is not None and value != "":
                try:
                    value = int(value)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(f"Invalid {name} configuration") from exc
                if not minimum <= value <= maximum:
                    raise RuntimeError(f"Invalid {name} configuration")
                return value
    for name in names:
        value = os.environ.get(name)
        if value is not None and value != "":
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Invalid {name} configuration") from exc
            if not minimum <= value <= maximum:
                raise RuntimeError(f"Invalid {name} configuration")
            return value
    return default


def hash_key(raw):
    """Return the bcrypt digest of ``raw``.

    bcrypt is salted and deliberately slow, so the digest cannot be used to
    *find* a key — only to verify one that has already been located. Use
    :func:`key_lookup` for the indexed lookup and :func:`verify_key` to check.
    """
    if not isinstance(raw, str):
        raise TypeError("API key must be text")
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_key(raw, digest):
    """Constant-time check of ``raw`` against a stored bcrypt ``digest``.

    Returns ``False`` (never raises) for malformed or missing digests so a
    corrupt row can never authenticate and never leaks a stack trace.
    """
    if not isinstance(raw, str) or not isinstance(digest, str):
        return False
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), digest.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def key_prefix(raw):
    """Leading characters of ``raw``, safe to store and show for identification."""
    if not isinstance(raw, str):
        raise TypeError("API key must be text")
    return raw[:KEY_PREFIX_LENGTH]


def _lookup_secret():
    """Key material for the deterministic API-key lookup HMAC.

    Tied to the session secret so no new secret has to be provisioned; rotating
    it invalidates issued keys exactly as it already invalidates sessions.
    """
    if has_app_context():
        secret = current_app.config.get("SECRET_KEY")
        if isinstance(secret, str) and secret:
            return secret
    for name in ("SESSION_SECRET", "SECRET_KEY"):
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError("A configured secret is required to derive API key lookups")


def key_lookup(raw):
    """Deterministic, secret-keyed index of ``raw`` (hex HMAC-SHA256).

    Unlike the bcrypt digest this is stable for a given raw key, which is what
    lets verification be a single indexed query. It is safe to store: an
    attacker with the database still cannot check guessed keys without the
    server-side secret, and the underlying HMAC is one-way regardless.
    """
    if not isinstance(raw, str):
        raise TypeError("API key must be text")
    return hmac.new(
        _lookup_secret().encode("utf-8"), raw.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _as_utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_expired(value, now=None):
    value = _as_utc(value)
    if value is None:
        return False
    return value <= (now or datetime.now(timezone.utc))


def is_revoked(record):
    return record.revoked_at is not None


def key_is_active(record, now=None):
    return not is_revoked(record) and not is_expired(record.expires_at, now)


def active_key_count(user_id, now=None):
    from app.models.api_key import ApiKey

    now = now or datetime.now(timezone.utc)
    return (
        ApiKey.select()
        .where(
            (ApiKey.user == user_id)
            & ApiKey.revoked_at.is_null(True)
            & (ApiKey.expires_at.is_null(True) | (ApiKey.expires_at > now))
        )
        .count()
    )


def max_active_api_keys():
    return _config_int(
        ("MAX_ACTIVE_API_KEYS", "API_KEY_MAX_ACTIVE", "API_KEY_MAX_ACTIVE_KEYS"),
        DEFAULT_MAX_ACTIVE_API_KEYS,
    )


def issue_api_key(user_id, name="", expires_at=None, enforce_limit=True):
    from app.models.api_key import ApiKey
    from app.models.user import User

    normalized_expiry = _as_utc(expires_at) if expires_at is not None else None
    if expires_at is not None and normalized_expiry is None:
        raise ValueError("expires_at must be a valid datetime")
    if normalized_expiry is not None and normalized_expiry <= datetime.now(timezone.utc):
        raise ValueError("expires_at must be in the future")
    raw = secrets.token_urlsafe(32)
    values = {
        "user_id": user_id,
        "key_hash": hash_key(raw),
        "key_prefix": key_prefix(raw),
        "key_lookup": key_lookup(raw),
        "name": (name or "").strip(),
        "expires_at": normalized_expiry,
    }
    with ApiKey._meta.database.atomic():
        try:
            try:
                User.select(User.id).where(User.id == user_id).for_update().get()
            except ValueError:
                User.select(User.id).where(User.id == user_id).get()
        except User.DoesNotExist as exc:
            raise ValueError("User does not exist") from exc
        if enforce_limit and active_key_count(user_id) >= max_active_api_keys():
            raise ActiveApiKeyLimitReached("Active API key limit reached")
        record = ApiKey.create(**values)
    return record, raw


def _last_used_interval():
    return _config_int(
        ("API_KEY_LAST_USED_THROTTLE_SECONDS", "LAST_USED_THROTTLE_SECONDS"),
        DEFAULT_LAST_USED_THROTTLE_SECONDS,
    )


def _touch_api_key(record):
    from app.models.api_key import ApiKey

    now = datetime.now(timezone.utc)
    previous = _as_utc(record.last_used_at)
    if previous is not None and previous >= now - timedelta(seconds=_last_used_interval()):
        return
    cutoff = now - timedelta(seconds=_last_used_interval())
    try:
        updated = (
            ApiKey.update(last_used_at=now)
            .where(
                (ApiKey.id == record.id)
                & ApiKey.revoked_at.is_null(True)
                & (ApiKey.last_used_at.is_null(True) | (ApiKey.last_used_at < cutoff))
            )
            .execute()
        )
        if updated:
            record.last_used_at = now
    except Exception:
        if has_app_context():
            current_app.logger.warning("API key last-used update failed", exc_info=True)


def _bearer_value():
    header = request.headers.get("Authorization", "")
    if not header:
        return None, False
    scheme, separator, raw = header.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None, True
    raw = raw.strip()
    if not raw or len(raw) > 512 or any(char.isspace() for char in raw):
        return None, True
    return raw, True


def authenticate_bearer(raw):
    from app.models.api_key import ApiKey
    from app.models.user import User

    if not isinstance(raw, str) or not raw or len(raw) > 512:
        return None
    raw = raw.strip()
    if not raw or any(char.isspace() for char in raw):
        return None
    try:
        lookup = key_lookup(raw)
    except RuntimeError:
        return None
    record = ApiKey.get_or_none(ApiKey.key_lookup == lookup)
    if record is None or not key_is_active(record):
        return None
    if not verify_key(raw, record.key_hash):
        return None
    try:
        User.get_by_id(record.user_id)
    except User.DoesNotExist:
        return None
    _touch_api_key(record)
    return record


def authenticate_session():
    from app.models.api_key import ApiKey
    from app.models.user import User

    if "user_id" not in session or "api_key_id" not in session:
        return None
    user_id = session.get("user_id")
    key_id = session.get("api_key_id")
    if isinstance(user_id, bool) or isinstance(key_id, bool):
        return None
    try:
        user_id = int(user_id)
        key_id = int(key_id)
    except (TypeError, ValueError):
        return None
    record = ApiKey.get_or_none(ApiKey.id == key_id)
    if record is None or record.user_id != user_id or not key_is_active(record):
        return None
    try:
        User.get_by_id(user_id)
    except User.DoesNotExist:
        return None
    _touch_api_key(record)
    return record


def authenticate_request():
    raw, supplied = _bearer_value()
    if supplied:
        if raw is None:
            return None
        record = authenticate_bearer(raw)
        return (record, "bearer") if record is not None else (None, "bearer")
    record = authenticate_session()
    return (record, "session") if record is not None else None


def _allowed_origins():
    configured = []
    if has_app_context():
        raw = current_app.config.get("SESSION_ALLOWED_ORIGINS", "")
    else:
        raw = os.environ.get("SESSION_ALLOWED_ORIGINS", "")
    if isinstance(raw, str):
        configured = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple, set)):
        configured = [str(item).strip().rstrip("/") for item in raw if str(item).strip()]
    if has_request_context():
        configured.append(request.host_url.rstrip("/"))
    return set(configured)


def _validate_same_origin():
    origin = request.headers.get("Origin", "").strip().rstrip("/")
    if origin and origin not in _allowed_origins():
        abort(403, description="Cross-origin request rejected")
    referer = request.headers.get("Referer", "").strip()
    if not origin and referer:
        from urllib.parse import urlsplit

        parsed = urlsplit(referer)
        candidate = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if candidate and candidate not in _allowed_origins():
            abort(403, description="Cross-origin request rejected")


def _csrf_token():
    token = session.get("csrf_token")
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def require_csrf():
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    _validate_same_origin()
    expected = session.get("csrf_token")
    provided = request.headers.get("X-CSRF-Token", "") or request.headers.get("X-CSRFToken", "")
    if not isinstance(expected, str) or not isinstance(provided, str) or not provided:
        abort(403, description="CSRF token required")
    if not hmac.compare_digest(expected, provided):
        abort(403, description="CSRF token required")


def require_auth(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        authenticated = authenticate_request()
        if authenticated is None:
            abort(401, description="Missing or invalid API key")
        record, method = authenticated
        if record is None:
            abort(401, description="Missing or invalid API key")
        g.api_key_id = record.id
        g.current_user_id = record.user_id
        g.auth_method = method
        if method == "session":
            require_csrf()
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception:
            if method == "session":
                session.clear()
            raise

    return wrapper


def require_admin(fn):
    @require_auth
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin():
            abort(403, description="Administrator access required")
        return fn(*args, **kwargs)

    return wrapper


def is_admin():
    user_id = getattr(g, "current_user_id", None)
    if user_id is None:
        return False
    from app.models.user import User

    return User.select(User.id).where((User.id == user_id) & User.is_admin).exists()


def assert_owner(user_id):
    if user_id != g.current_user_id and not is_admin():
        abort(404)


require_owner = assert_owner


def scope_query(query, owner_field):
    return query if is_admin() else query.where(owner_field == g.current_user_id)


def cache_scope():
    return f"actor={g.current_user_id}:admin={int(is_admin())}:"


def create_session_for_key(record):
    session.clear()
    session["user_id"] = record.user_id
    session["api_key_id"] = record.id
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    return _csrf_token()


def clear_auth_session():
    session.clear()


def no_store(response):
    response.headers["Cache-Control"] = "no-store, private"
    return response
