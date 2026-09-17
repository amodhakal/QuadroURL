"""Ownership-scoped organization, search and atomic activation operations."""

import json
from datetime import datetime, timezone
from typing import Annotated

from flask import Blueprint, abort, jsonify
from peewee import JOIN, fn
from pydantic import Field, StrictBool, StringConstraints, field_validator
from werkzeug.security import generate_password_hash

from app.cache import clear_list_cache, delete_url, delete_url_by_short_code
from app.database import models
from app.models.url import Url
from app.utils.auth import assert_owner, require_auth, scope_query
from app.utils.pagination import envelope, paginate
from app.utils.ratelimit import rate_limit
from app.utils.schemas import Body, PositiveId, UrlQuery, parse_body, parse_query

links_bp = Blueprint("links", __name__)
LinkMetadata = models.LinkMetadata


@links_bp.after_app_request
def protect_link_responses(response):
    from flask import request
    from app.utils.link_access import private_response

    endpoint = (request.endpoint or "").replace("_v1.", ".")
    if endpoint in {"urls.redirect_short_code", "urls.redirect_short_code_legacy", "qr.scan"}:
        return private_response(response)
    if request.blueprint in {"links", "links_v1", "qr", "qr_v1"}:
        return private_response(response)
    return response


Label = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=80)]
Tag = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=40, pattern=r"^[\w -]+$"
    ),
]


class MetadataUpdate(Body):
    tags: Annotated[list[Tag], Field(max_length=20)] = Field(default=None)
    folder: Label = Field(default=None)
    password: Annotated[str, StringConstraints(strict=True, min_length=8, max_length=128)] | (
        None
    ) = None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value):
        return sorted(set(tag.lower() for tag in value))


class LinkQuery(UrlQuery):
    q: Annotated[str, Field(max_length=255)] | None = None
    tag: Tag | None = None
    folder: Label | None = None


class BulkActivation(Body):
    ids: Annotated[list[PositiveId], Field(min_length=1, max_length=100)]
    is_active: StrictBool


def owned_link(url_id):
    link = Url.get_or_none(Url.id == url_id)
    if link is None:
        abort(404)
    assert_owner(link.user_id)
    return link


def metadata_dict(metadata):
    return {
        "tags": json.loads(metadata.tags) if metadata else [],
        "folder": metadata.folder if metadata else "",
        "password_protected": bool(metadata and metadata.password_hash),
    }


def invalidate(link):
    delete_url(link.id)
    delete_url_by_short_code(link.short_code)
    clear_list_cache("list:urls:")


@links_bp.route("/links/<int:url_id>/metadata", methods=["GET", "PATCH"])
@require_auth
@rate_limit(capacity=30, refill_rate=0.5)
def metadata(url_id):
    from flask import request

    owned_link(url_id)
    if request.method == "GET":
        return jsonify(metadata_dict(LinkMetadata.get_or_none(LinkMetadata.url == url_id)))
    data = parse_body(MetadataUpdate)
    values = {"updated_at": datetime.now(timezone.utc)}
    if "tags" in data:
        values["tags"] = json.dumps(data["tags"], ensure_ascii=False)
    if "folder" in data:
        values["folder"] = data["folder"]
    if "password" in data:
        values["password_hash"] = (
            generate_password_hash(data["password"]) if data["password"] else ""
        )
    with Url._meta.database.atomic():
        # Partial update rather than read/modify/write preserves independent edits.
        LinkMetadata.insert(url=url_id).on_conflict_ignore().execute()
        LinkMetadata.update(**values).where(LinkMetadata.url == url_id).execute()
    return jsonify(metadata_dict(LinkMetadata.get_by_id(url_id)))


@links_bp.get("/links")
@require_auth
@rate_limit(capacity=300, refill_rate=5)
def search_links():
    from app.routes.urls import format_url

    params = parse_query(LinkQuery)
    query = scope_query(Url.select(Url, LinkMetadata).join(LinkMetadata, JOIN.LEFT_OUTER), Url.user)
    for name in ("id", "user_id", "short_code", "original_url", "is_active"):
        value = getattr(params, name)
        if value is not None:
            query = query.where(getattr(Url, name) == value)
    if params.q:
        query = query.where(
            Url.title.contains(params.q)
            | Url.original_url.contains(params.q)
            | Url.short_code.contains(params.q)
        )
    if params.folder is not None:
        query = query.where(fn.COALESCE(LinkMetadata.folder, "") == params.folder)
    if params.tag is not None:
        # JSON-quoted canonical token gives exact matching, not substring tags.
        query = query.where(
            LinkMetadata.tags.contains(json.dumps(params.tag.lower(), ensure_ascii=False))
        )
    rows, has_more = paginate(query, Url.id, params)
    items = [
        dict(format_url(row), **metadata_dict(getattr(row, "linkmetadata", None))) for row in rows
    ]
    return jsonify(envelope(items, params, has_more, {"items": items, "has_more": has_more}))


@links_bp.post("/links/bulk-activation")
@require_auth
@rate_limit(capacity=30, refill_rate=0.5)
def bulk_activation():
    data = parse_body(BulkActivation)
    ids = sorted(set(data["ids"]))
    with Url._meta.database.atomic():
        query = scope_query(Url.select().where(Url.id.in_(ids)), Url.user)
        links = list(query)
        if len(links) != len(ids):
            abort(404)  # All-or-nothing, no disclosure of which IDs exist.
        Url.update(is_active=data["is_active"], updated_at=datetime.now(timezone.utc)).where(
            Url.id.in_(ids)
        ).execute()
    for link in links:
        invalidate(link)
    return jsonify({"ids": ids, "is_active": data["is_active"], "updated": len(ids)})
