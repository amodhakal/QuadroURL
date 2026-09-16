"""Shared request schemas (pydantic) plus legacy-compatible error shape.

Every schema call aborts 400 with ``{"error": "..."}`` — the message text is
kept close to the pre-existing manual checks so clients parsing ``error``
strings see familiar text. Backed by pydantic v2 (issue #191): str|int|bool
discrimination, length caps, ISO datetime with timezone, and unknown-field
rejection all come from the schema, not per-route ``isinstance`` chains.
"""

from flask import abort, current_app, request
from pydantic import BaseModel, ConfigDict, ValidationError


def _abort_validation(exc: ValidationError, log_message: str):
    first = exc.errors()[0]
    loc = ".".join(str(p) for p in first.get("loc", ()) if p not in ("body",))
    msg = first.get("msg", "Invalid input")
    if loc:
        description = f"{loc}: {msg}"
    else:
        description = msg
    current_app.logger.warning(f"{log_message}: {description}")
    abort(400, description=description)


def parse_body(schema, log_message="Invalid JSON body"):
    """Parse the JSON body against ``schema``; abort 400 with a field message.

    Extra keys are rejected (equivalent to the old ``reject_unknown_fields``).
    """
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        current_app.logger.warning(log_message)
        abort(400, description="Invalid JSON")
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        _abort_validation(exc, log_message)


def parse_query(schema):
    """Parse query string against ``schema``; abort 400 on failure."""
    try:
        return schema.model_validate(request.args.to_dict())
    except ValidationError as exc:
        _abort_validation(exc, "Invalid query parameters")
