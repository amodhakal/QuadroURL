"""Authenticated quarantine administration and owner-scoped milestone webhooks."""

import base64
import json
from uuid import uuid4

from flask import Blueprint, abort, g, jsonify, request

from app.database import db, models
from app.models.url import Url
from app.utils.auth import assert_owner, require_admin, require_auth
from shared.delivery_models import create_delivery_models
from shared.delivery_worker import allowed_topics, validate_destination


delivery_bp = Blueprint("delivery", __name__)
delivery = create_delivery_models(db, models)


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, description="Expected a JSON object")
    return data


@delivery_bp.get("/admin/dead-letters")
@require_admin
def dead_letters():
    try:
        after = int(request.args.get("after", 0))
    except ValueError:
        abort(400)
    rows = (
        delivery.DeadLetter.select()
        .where(delivery.DeadLetter.id > after)
        .order_by(delivery.DeadLetter.id)
        .limit(100)
    )
    return jsonify(
        [
            dict(
                id=row.id,
                topic=row.topic,
                partition=row.partition,
                offset=row.offset,
                error=row.error,
                created_at=row.created_at,
                payload_base64=base64.b64encode(row.payload or b"").decode("ascii"),
            )
            for row in rows
        ]
    )


@delivery_bp.post("/admin/dead-letters/<int:letter_id>/replay")
@require_admin
def replay_dead_letter(letter_id):
    data = body()
    if set(data) - {"payload"}:
        abort(400, description="Only an optional corrected payload is accepted")
    letter = delivery.DeadLetter.get_or_none(delivery.DeadLetter.id == letter_id)
    if letter is None:
        abort(404)
    if letter.topic not in allowed_topics():
        abort(400, description="Source topic is not replayable")
    if "payload" in data and not isinstance(data["payload"], dict):
        abort(400, description="Corrected payload must be a JSON object")
    payload = json.dumps(data["payload"]).encode("utf-8") if "payload" in data else letter.payload
    if payload is not None and len(payload) > 1024 * 1024:
        abort(413)
    with db.atomic():
        row, created = delivery.Replay.get_or_create(
            dead_letter=letter,
            defaults={
                "id": str(uuid4()),
                "payload": payload,
                "requested_by": g.current_user_id,
            },
        )
        if not created and row.payload != payload:
            abort(409, description="Replay already exists with different payload")
    return jsonify(id=row.id, state=row.state), 202 if created else 200


@delivery_bp.get("/admin/replays/<replay_id>")
@require_admin
def replay_status(replay_id):
    row = delivery.Replay.get_or_none(delivery.Replay.id == replay_id)
    if row is None:
        abort(404)
    return jsonify(id=row.id, state=row.state, attempts=row.attempts, last_error=row.last_error)


def owned_url(url_id):
    url = Url.get_or_none(Url.id == url_id)
    if url is None:
        abort(404)
    assert_owner(url.user_id)
    return url


def subscription_json(row):
    return dict(
        id=row.id,
        url_id=row.url_id,
        destination=row.destination,
        milestone=row.milestone,
        enabled=row.enabled,
    )


@delivery_bp.route("/urls/<int:url_id>/webhooks", methods=["GET", "POST"])
@require_auth
def subscriptions(url_id):
    owned_url(url_id)
    if request.method == "GET":
        return jsonify(
            [
                subscription_json(row)
                for row in delivery.Subscription.select()
                .where(delivery.Subscription.url == url_id)
                .order_by(delivery.Subscription.id)
                .limit(100)
            ]
        )
    data = body()
    if set(data) != {"destination", "milestone"}:
        abort(400, description="Expected destination and milestone")
    milestone = data["milestone"]
    if type(milestone) is not int or not 1 <= milestone <= 2**63 - 1:
        abort(400, description="Milestone must be a positive integer")
    try:
        validate_destination(data["destination"])
    except ValueError:
        abort(400, description="Destination must be an operator-approved HTTPS URL")
    with db.atomic():
        # Serialize registrations per URL and bound fanout.
        Url.update(updated_at=Url.updated_at).where(Url.id == url_id).execute()
        existing = delivery.Subscription.get_or_none(
            (delivery.Subscription.url == url_id)
            & (delivery.Subscription.destination == data["destination"])
            & (delivery.Subscription.milestone == milestone)
        )
        if existing:
            return jsonify(subscription_json(existing)), 200
        if delivery.Subscription.select().where(delivery.Subscription.url == url_id).count() >= 100:
            abort(409, description="At most 100 webhooks per URL")
        row = delivery.Subscription.create(
            url_id=url_id, destination=data["destination"], milestone=milestone
        )
    return jsonify(subscription_json(row)), 201


@delivery_bp.delete("/urls/<int:url_id>/webhooks/<int:subscription_id>")
@require_auth
def disable_subscription(url_id, subscription_id):
    owned_url(url_id)
    changed = (
        delivery.Subscription.update(enabled=False)
        .where(
            (delivery.Subscription.url == url_id) & (delivery.Subscription.id == subscription_id)
        )
        .execute()
    )
    if not changed:
        abort(404)
    return "", 204


@delivery_bp.get("/urls/<int:url_id>/webhook-deliveries")
@require_auth
def delivery_status(url_id):
    owned_url(url_id)
    rows = (
        delivery.Delivery.select()
        .join(delivery.Subscription)
        .where(delivery.Subscription.url == url_id)
        .order_by(delivery.Delivery.id)
        .limit(100)
    )
    return jsonify(
        [
            dict(
                id=row.id,
                subscription_id=row.subscription_id,
                state=row.state,
                attempts=row.attempts,
                last_error=row.last_error,
            )
            for row in rows
        ]
    )
