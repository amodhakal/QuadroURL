import json

from flask import Blueprint, abort, current_app, jsonify, request
from playhouse.shortcuts import model_to_dict

from app.cache import (
    clear_list_cache,
    get_list_cache,
    get_url,
    get_user,
    set_list_cache,
)
from app.models.event import Event
from app.utils.ratelimit import rate_limit
from app.utils.validation import (
    require_dict,
    require_int,
    require_json,
    require_str,
    validate_offset_params,
)


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
def list_events():
    offset = request.args.get("offset", 0, type=int)
    size = request.args.get("size", 20, type=int)
    offset, size = validate_offset_params(offset, size)

    key_parts = [f"offset={offset}", f"size={size}"]
    for name in ("url_id", "user_id", "event_type", "before_id"):
        if name in request.args:
            key_parts.append(f"{name}={request.args[name][:128]}")
    cache_key = "list:events:" + "&".join(key_parts)
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

    if "url_id" in request.args:
        query = query.where(Event.url == _require_int_query_param("url_id"))
    if "user_id" in request.args:
        query = query.where(Event.user == _require_int_query_param("user_id"))
    if "event_type" in request.args:
        query = query.where(Event.event_type == request.args["event_type"])

    if "before_id" in request.args:
        query = query.where(Event.id < _require_int_query_param("before_id"))
        query = query.order_by(Event.id.desc()).limit(size)
        rows = list(query)
    else:
        query = query.order_by(Event.id).limit(size).offset(offset)
        rows = list(query)

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
    set_list_cache(cache_key, result)
    return jsonify(result)


@events_bp.route("/events", methods=["POST"])
@rate_limit(capacity=300, refill_rate=5.0)
def create_event():
    data = require_json("Invalid JSON received for create_event")

    url_id = data.get("url_id")
    user_id = data.get("user_id")
    event_type = data.get("event_type")
    details = data.get("details", {})

    require_dict(details, "details must be an object", "details must be an object")

    require_int(url_id, "url_id must be an integer", "url_id must be an integer")
    require_int(user_id, "user_id must be an integer", "user_id must be an integer")
    require_str(event_type, "event_type must be a string", "event_type must be a string")

    if get_url(url_id) is None:
        current_app.logger.warning("URL not found")
        abort(400, description="URL not found")

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
