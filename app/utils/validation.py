"""Shared request-validation helpers.

Consolidates the duplicated validation patterns from the users/urls/events
route modules. Every helper aborts with the exact message/status the routes
used before, so error contracts are unchanged.

Contract note (bool vs int): ``require_int`` deliberately uses
``isinstance(value, int)`` without excluding ``bool`` (``bool`` is a
subclass of ``int``). That preserves the routes' long-standing semantics:
``True`` passes as an int, while falsy values (``0``, ``False``, ``None``,
``""``) fail via the ``not value`` guard. See the regression tests in
``tests/test_validation.py``.
"""

from datetime import datetime, timezone

from flask import abort, current_app, request


def require_json(log_message):
    """Return the parsed JSON body or abort 400 ``Invalid JSON``.

    ``log_message`` is the route-specific warning string (e.g.
    ``"Invalid JSON received for create_user"``) emitted before aborting.
    """
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        current_app.logger.warning(log_message)
        abort(400, description="Invalid JSON")
    return data


def validate_page_params(page, per_page):
    """Validate users-style ``page``/``per_page`` params (page>=1, 1-100)."""
    if page is None or page < 1:
        abort(400, description="page must be >= 1")
    if per_page is None or per_page < 1 or per_page > 100:
        abort(400, description="per_page must be between 1 and 100")
    return page, per_page


def validate_offset_params(offset, size):
    """Validate urls/events-style ``offset``/``size`` params (offset>=0, 1-100)."""
    if offset is None or offset < 0:
        abort(400, description="offset must be >= 0")
    if size is None or size < 1 or size > 100:
        abort(400, description="size must be between 1 and 100")
    return offset, size


def require_non_empty_str(value, message, log_message=None):
    """Abort 400 unless ``value`` is a string with non-whitespace content."""
    if not value or not isinstance(value, str) or not value.strip():
        if log_message is not None:
            current_app.logger.warning(log_message)
        abort(400, description=message)
    return value


def require_str(value, message, log_message=None):
    """Abort 400 unless ``value`` is a truthy string (no blank check)."""
    if not value or not isinstance(value, str):
        if log_message is not None:
            current_app.logger.warning(log_message)
        abort(400, description=message)
    return value


def require_int(value, message, log_message=None):
    """Abort 400 unless ``value`` is a truthy int.

    ``bool`` is intentionally accepted (``isinstance(True, int)``), matching
    the pre-existing route checks. Falsy values (``0``, ``False``, ``None``)
    fail via the ``not value`` guard.
    """
    if not value or not isinstance(value, int):
        if log_message is not None:
            current_app.logger.warning(log_message)
        abort(400, description=message)
    return value


def require_bool(value, message, log_message=None):
    """Abort 400 unless ``value`` is a ``bool``."""
    if not isinstance(value, bool):
        if log_message is not None:
            current_app.logger.warning(log_message)
        abort(400, description=message)
    return value


def require_dict(value, message, log_message=None):
    """Abort 400 unless ``value`` is a ``dict``."""
    if not isinstance(value, dict):
        if log_message is not None:
            current_app.logger.warning(log_message)
        abort(400, description=message)
    return value


def reject_unknown_fields(data, allowed):
    """Abort 400 ``Unknown fields: [...]`` when ``data`` has extra keys."""
    unknown = set(data) - allowed
    if unknown:
        abort(400, description=f"Unknown fields: {sorted(unknown)}")
    return data


def parse_expires_at(value):
    """Validate an optional ``expires_at`` ISO 8601 value (#192, #134).

    Returns an aware ``datetime`` when ``value`` is a future-dated string, or
    ``None`` when ``value`` is ``None`` (absent/null means "never expires").
    Aborts 400 on non-string input, unparseable input, timezone-naive input
    (callers must be explicit — naive values are rejected rather than
    silently assumed UTC), or non-future dates.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        abort(400, description="expires_at must be an ISO 8601 datetime string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        abort(400, description="expires_at must be an ISO 8601 datetime string")
    if parsed.tzinfo is None:
        abort(400, description="expires_at must include timezone info")
    if parsed <= datetime.now(timezone.utc):
        abort(400, description="expires_at must be in the future")
    return parsed
