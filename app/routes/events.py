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


@events_bp.route("/events", methods=["GET"])
def list_events():
    cache_key = f"list:events:{request.query_string.decode()}"
    cached = get_list_cache(cache_key)
    if cached is not None:
        return jsonify(cached)

    offset = request.args.get("offset", 0, type=int)
    size = request.args.get("size", 20, type=int)

    query = Event.select(
        Event.id,
        Event.url,
        Event.user,
        Event.event_type,
        Event.timestamp,
        Event.details,
    )

    if "url_id" in request.args:
        query = query.where(Event.url == request.args.get("url_id", type=int))
    if "user_id" in request.args:
        query = query.where(Event.user == request.args.get("user_id", type=int))
    if "event_type" in request.args:
        query = query.where(Event.event_type == request.args["event_type"])

    if "before_id" in request.args:
        query = query.where(Event.id < request.args.get("before_id", type=int))
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
def create_event():
    data = request.get_json(silent=True)
    if not data:
        current_app.logger.warning("Invalid JSON received for create_event")
        abort(400, description="Invalid JSON")

    url_id = data.get("url_id")
    user_id = data.get("user_id")
    event_type = data.get("event_type")
    details = data.get("details", {})

    if not isinstance(details, dict):
        current_app.logger.warning("details must be an object")
        abort(400, description="details must be an object")

    if not url_id or not isinstance(url_id, int):
        current_app.logger.warning("url_id must be an integer")
        abort(400, description="url_id must be an integer")
    if not user_id or not isinstance(user_id, int):
        current_app.logger.warning("user_id must be an integer")
        abort(400, description="user_id must be an integer")
    if not event_type or not isinstance(event_type, str):
        current_app.logger.warning("event_type must be a string")
        abort(400, description="event_type must be a string")

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
