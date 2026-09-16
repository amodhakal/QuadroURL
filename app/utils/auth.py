"""Bearer-key authentication, phase 1 (#99).

Every non-public endpoint requires ``Authorization: Bearer <key>`` via
:func:`require_auth`, which loads the key's owner into ``flask.g``.
Public by necessity: ``/health``, ``/ready``, observability endpoints,
the redirect/expand product surface, the async status poll (unguessable
request id), the chaos switch (own token gate), and user registration
itself (bootstrap — registration issues the first key).

Deliberately NOT here yet: object-level ownership (any valid key can
mutate any row — no worse than today, where no key is needed at all).
That matrix belongs to the ownership model (#175); this module is the
enforcement point it will hook into (``g.current_user_id`` is already
the authenticated identity).
"""

import functools
import hashlib
import secrets

from flask import abort, current_app, g, request


def hash_key(raw):
    """sha256 hex digest — the only form ever persisted."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_api_key(user_id, name=""):
    """Create a key row and return ``(record, raw_key)``.

    The raw key is returned once for display; only its digest is stored.
    """
    from app.models.api_key import ApiKey

    raw = secrets.token_urlsafe(32)
    record = ApiKey.create(user_id=user_id, key_hash=hash_key(raw), name=name or "")
    return record, raw


def require_auth(fn):
    """Abort 401 unless a valid bearer key identifies the caller."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        scheme, _, raw = header.partition(" ")
        if scheme.lower() != "bearer" or not raw.strip():
            current_app.logger.warning("Missing or malformed Authorization header")
            abort(401, description="Missing or invalid API key")
        from app.models.api_key import ApiKey

        record = ApiKey.get_or_none(ApiKey.key_hash == hash_key(raw.strip()))
        if record is None:
            current_app.logger.warning("Unknown API key presented")
            abort(401, description="Missing or invalid API key")
        g.api_key_id = record.id
        g.current_user_id = record.user_id
        return fn(*args, **kwargs)

    return wrapper
