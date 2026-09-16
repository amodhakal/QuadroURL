"""Bounded, ownership-scoped CSV/JSON exports (#185)."""

import csv
import io

from flask import Blueprint, Response, abort, jsonify, request

from app.models import Event, Url, User
from app.utils.auth import require_auth, scope_query
from app.utils.ratelimit import rate_limit

exports_bp = Blueprint("exports", __name__)
MAX_ROWS = 1000
FIELDS = {
    "users": (User, User.id, ("id", "username", "email", "created_at")),
    "urls": (
        Url,
        Url.user,
        (
            "id",
            "user_id",
            "short_code",
            "original_url",
            "title",
            "is_active",
            "expires_at",
            "created_at",
            "updated_at",
        ),
    ),
    "events": (
        Event,
        Event.user,
        ("id", "url_id", "user_id", "event_type", "timestamp", "details"),
    ),
}


def _integer(name, default, minimum, maximum):
    raw = request.args.get(name, str(default))
    try:
        value = int(raw)
    except (ValueError, TypeError):
        abort(400, description=f"{name} must be an integer")
    if not minimum <= value <= maximum:
        abort(400, description=f"{name} must be between {minimum} and {maximum}")
    return value


def _serialize(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _csv_cell(value):
    if value is None:
        return ""
    text = str(value)
    # Quoting CSV does not prevent spreadsheet formulas from executing.
    if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


@exports_bp.route("/exports/<resource>", methods=["GET"])
@require_auth
@rate_limit(capacity=10, refill_rate=1.0)
def export_rows(resource):
    if resource not in FIELDS:
        abort(404)
    if set(request.args) - {"format", "limit", "after_id"}:
        abort(400, description="Unknown export parameter")
    output_format = request.args.get("format", "json")
    if output_format not in {"json", "csv"}:
        abort(400, description="format must be json or csv")
    limit = _integer("limit", MAX_ROWS, 1, MAX_ROWS)
    after_id = _integer("after_id", 0, 0, 2**63 - 1)
    model, owner, fields = FIELDS[resource]
    rows = list(
        scope_query(model.select(), owner)
        .where(model.id > after_id)
        .order_by(model.id)
        .limit(limit + 1)
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    data = [{field: _serialize(getattr(row, field)) for field in fields} for row in rows]
    next_id = rows[-1].id if has_more else None
    if output_format == "json":
        response = jsonify(
            data=data, pagination={"limit": limit, "has_more": has_more, "next_after_id": next_id}
        )
    else:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(fields)
        for row in data:
            writer.writerow([_csv_cell(row[field]) for field in fields])
        response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{resource}.{output_format}"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Has-More"] = str(has_more).lower()
    if next_id is not None:
        response.headers["X-Next-After-Id"] = str(next_id)
    return response
