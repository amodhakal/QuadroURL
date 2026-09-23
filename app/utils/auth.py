"""Bearer authentication and ownership policy.

Admin membership is stored on the user record and is never supplied by
registration, update input, or client headers.
"""

import functools
import hashlib
import secrets

from flask import abort, g, request


def hash_key(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_api_key(user_id, name=""):
    """Store only the digest; return the raw credential once."""
    from app.models.api_key import ApiKey

    raw = secrets.token_urlsafe(32)
    record = ApiKey.create(user_id=user_id, key_hash=hash_key(raw), name=name or "")
    return record, raw


def is_admin():
    user_id = getattr(g, "current_user_id", None)
    if user_id is None:
        return False
    from app.models.user import User

    return User.select(User.id).where((User.id == user_id) & User.is_admin).exists()


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
