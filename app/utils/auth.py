"""Bearer authentication and ownership policy.

Admin membership is operator-managed through ADMIN_USER_IDS (comma-separated
numeric IDs), never through registration/update input or client headers.
"""

import functools
import hashlib
import os
import secrets

from flask import abort, current_app, g, request


def hash_key(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_api_key(user_id, name=""):
    """Store only the digest; return the raw credential once."""
    from app.models.api_key import ApiKey

    raw = secrets.token_urlsafe(32)
    record = ApiKey.create(user_id=user_id, key_hash=hash_key(raw), name=name or "")
    return record, raw


def is_admin():
    configured = current_app.config.get("ADMIN_USER_IDS", os.getenv("ADMIN_USER_IDS", ""))
    ids = configured.split(",") if isinstance(configured, str) else (configured or [])
    user_id = getattr(g, "current_user_id", None)
    return user_id is not None and str(user_id) in {str(i).strip() for i in ids if str(i).strip()}


def assert_owner(user_id):
    """Hide foreign resources; check before reading even a shared object cache."""
    if user_id != g.current_user_id and not is_admin():
        abort(404)


# Compatibility with the initial ownership implementation.
require_owner = assert_owner


def scope_query(query, owner_field):
    return query if is_admin() else query.where(owner_field == g.current_user_id)


def cache_scope():
    return f"actor={g.current_user_id}:admin={int(is_admin())}:"


def require_auth(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        scheme, _, raw = header.partition(" ")
        if scheme.lower() != "bearer" or not raw.strip():
            abort(401, description="Missing or invalid API key")
        from app.models.api_key import ApiKey
        from app.models.user import User

        record = ApiKey.get_or_none(ApiKey.key_hash == hash_key(raw.strip()))
        if record is None or not User.select().where(User.id == record.user_id).exists():
            abort(401, description="Missing or invalid API key")
        g.api_key_id = record.id
        g.current_user_id = record.user_id
        return fn(*args, **kwargs)

    return wrapper


def require_admin(fn):
    @require_auth
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin():
            abort(403, description="Administrator access required")
        return fn(*args, **kwargs)

    return wrapper
