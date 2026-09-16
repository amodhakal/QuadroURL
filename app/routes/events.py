import json

from flask import Blueprint, abort, current_app, jsonify, request
from playhouse.shortcuts import model_to_dict

from app.cache import (
    clear_list_cache,
    get_list_cache,
    get_user,
    set_list_cache,
)
from app.models.event import Event
from app.utils.ratelimit import rate_limit
from app.utils.auth import require_auth, require_owner, scope_query
from app.models.url import Url
from flask import g
from app.utils.schemas import EventCreate, EventQuery, parse_body, parse_query
from app.utils.pagination import bounds, cache_key as list_cache_key, paginate, envelope


events_bp = Blueprint("events", __name__)


def format_event(event):
    d = model_to_dict(event, recurse=False)
    d["url_id"] = d.pop("url")
    d["user_id"] = {"id": d.pop("user")}
    try:
        d["details"] = json.loads(d["details"])
    except (json.JSONDecodeError, TypeError):
        d["details"] = {}
    return d


def _require_int_query_param(name):
    """Parse an integer query param or abort 400 (#242).

    Same silent-None guard as the urls list route: present-but-unparseable
    values 400 instead of producing an ``IS NULL`` comparison.
    """
    value = request.args.get(name, type=int)
    if value is None:
        current_app.logger.warning(f"Invalid {name} query param: {request.args.get(name)!r}")
        abort(400, description=f"{name} must be an integer")
    return value


@events_bp.route("/events", methods=["GET"])
@rate_limit(capacity=300, refill_rate=5.0)
@require_auth
@rate_limit(capacity=300, refill_rate=5.0)
def list_events():
    params = parse_query(EventQuery)
    bounds(params)
    cache_key = list_cache_key("events", params)
    cached = get_list_cache(cache_key)
    if cached is not None:
        return jsonify(cached)

    query = Event.select(
        Event.id,
        Event.url,
        Event.user,
        Event.event_type,
        Event.timestamp,
        Event.details,
    )

    query = scope_query(query, Event.user)
    for name in ("url_id", "user_id", "event_type"):
        value = getattr(params, name)
        if value is not None:
            query = query.where(getattr(Event, name) == value)
    rows, has_more = paginate(query, Event.id, params)

    result = []
    for e in rows:
        details = {}
        try:
            details = json.loads(e.details) if e.details else {}
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(
            {
                "id": e.id,
                "url_id": e.url_id,
                "user_id": {"id": e.user_id},
                "event_type": e.event_type,
                "timestamp": e.timestamp.isoformat(),
                "details": details,
            }
        )
    result = envelope(result, params, has_more, result)
    set_list_cache(cache_key, result)
    return jsonify(result)


@events_bp.route("/events", methods=["POST"])
@require_auth
@rate_limit(capacity=300, refill_rate=5.0)
def create_event():
    data = parse_body(EventCreate)

    url_id = data.get("url_id")
    user_id = data.get("user_id", g.current_user_id)
    require_owner(user_id)
    event_type = data.get("event_type")
    details = data.get("details", {})

    url = Url.get_or_none(Url.id == url_id)
    if url is None:
        abort(404)
    require_owner(url.user_id)
    if url.user_id != user_id:
        abort(400, description="Event user must own the URL")

    if get_user(user_id) is None:
        current_app.logger.warning("User not found")
        abort(400, description="User not found")

    event = Event.create(
        url_id=url_id,
        user_id=user_id,
        event_type=event_type,
        details=json.dumps(details),
    )

    current_app.logger.info(f"Event created: type={event_type} url_id={url_id}")
    clear_list_cache("list:events:")
    return jsonify(format_event(event)), 201
