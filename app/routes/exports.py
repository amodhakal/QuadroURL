"""Bounded, ownership-scoped CSV/JSON exports (#185)."""

import csv
import io

from flask import Blueprint, Response, abort, jsonify

from app.models import Event, Url, User
from app.utils.auth import require_auth, scope_query
from app.utils.ratelimit import rate_limit
from app.utils.schemas import ExportQuery, parse_query

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
    query = parse_query(ExportQuery)
    output_format = query.format
    limit = query.limit
    after_id = query.after_id
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
