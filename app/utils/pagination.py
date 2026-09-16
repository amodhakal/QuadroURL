"""Bounded, deterministic pagination with legacy response compatibility."""

import hashlib
import json

from flask import abort, request

from app.utils.auth import cache_scope


def is_v1():
    return request.blueprint.endswith("_v1")


def bounds(params):
    if (params.page is not None or params.per_page is not None) and (
        "offset" in request.args or "size" in request.args or params.before_id is not None
    ):
        abort(400, description="Do not mix page and offset/cursor pagination")
    size = params.per_page or params.size
    offset = ((params.page or 1) - 1) * size if params.page or params.per_page else params.offset
    if offset > 100000:
        abort(400, description="offset must not exceed 100000; use before_id")
    if params.before_id is not None and offset:
        abort(400, description="Do not mix offset and before_id")
    return offset, size


def cache_key(resource, params):
    # Hash the complete validated filters, not truncated values that can collide.
    digest = hashlib.sha256(json.dumps(params.model_dump(), sort_keys=True).encode()).hexdigest()
    return f"list:{resource}:{cache_scope()}v1={int(is_v1())}:{digest}"


def paginate(query, id_field, params):
    offset, size = bounds(params)
    if params.before_id is not None:
        query = query.where(id_field < params.before_id).order_by(id_field.desc())
    else:
        query = query.order_by(id_field)
    rows = list(query.offset(offset).limit(size + 1))
    return rows[:size], len(rows) > size


def envelope(items, params, has_more, legacy):
    if not is_v1():
        return legacy
    offset, size = bounds(params)
    cursor = params.before_id is not None
    return {
        "items": items,
        "pagination": {
            "offset": offset,
            "size": size,
            "has_more": has_more,
            "next_offset": offset + size if has_more and not cursor else None,
            "next_before_id": items[-1]["id"] if has_more and cursor else None,
        },
    }
