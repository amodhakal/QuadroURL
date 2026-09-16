"""Bounded SVG QR generation and a tracked, policy-enforced scan destination."""

import io
import os
from typing import Annotated, Literal
from urllib.parse import urlsplit, quote

import segno
from flask import Blueprint, abort, current_app, jsonify, redirect, request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.routes.links import owned_link
from app.utils.auth import require_auth
from app.utils.link_access import private_response
from app.utils.ratelimit import rate_limit
from app.utils.schemas import parse_query
from app.utils.url_safety import validate_destination

qr_bp = Blueprint("qr", __name__)


class QrOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scale: int = Field(default=6, ge=1, le=20)
    border: int = Field(default=4, ge=4, le=16)
    error: Literal["L", "M", "Q", "H"] = "M"
    dark: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] = "#000000"
    light: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] = "#ffffff"


def _luminance(color):
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def _scan_url(short_code):
    # Never use the incoming Host header for downloadable QR destinations.
    base = current_app.config.get("PUBLIC_BASE_URL", os.getenv("PUBLIC_BASE_URL", ""))
    try:
        validate_destination(base)
        parsed = urlsplit(base)
        if (
            parsed.scheme != "https"
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin")
    except ValueError:
        abort(503, description="Configure PUBLIC_BASE_URL as a public HTTPS origin for QR codes")
    return base.rstrip("/") + "/q/" + quote(short_code, safe="")


@qr_bp.get("/links/<int:url_id>/qr.svg")
@require_auth
@rate_limit(capacity=30, refill_rate=0.5)
def svg_qr(url_id):
    link = owned_link(url_id)
    options = parse_query(QrOptions)
    dark = _luminance(options.dark)
    light = _luminance(options.light)
    if light <= dark or (light + 0.05) / (dark + 0.05) < 4.5:
        abort(400, description="QR requires dark modules on light background with contrast >= 4.5")
    scan_url = _scan_url(link.short_code)
    qr = segno.make(scan_url, error=options.error, micro=False)
    output = io.BytesIO()
    qr.save(
        output,
        kind="svg",
        scale=options.scale,
        border=options.border,
        dark=options.dark,
        light=options.light,
        xmldecl=False,
    )
    response = Response(output.getvalue(), mimetype="image/svg+xml")
    response.headers["Content-Disposition"] = f'attachment; filename="link-{link.id}.svg"'
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return private_response(response)


@qr_bp.route("/q/<short_code>", methods=["GET", "POST"])
@rate_limit(capacity=2000, refill_rate=200)
def scan(short_code):
    from app.routes.urls import resolve_short_code_or_404, track_click, is_bot_user_agent
    from app.utils.events import create_event

    data = resolve_short_code_or_404(short_code)
    # HEAD requests and crawlers aren't scans/clicks. Human counts are best effort,
    # not an assertion that an actual camera was used (the route is public).
    if request.method != "HEAD" and not is_bot_user_agent(request.headers.get("User-Agent", "")):
        try:
            create_event(data["id"], data["user_id"], "qr_scan", {"short_code": short_code})
        except Exception:
            current_app.logger.exception("QR scan tracking failed (redirect unaffected)")
        track_click(data, short_code)
    return private_response(redirect(data["original_url"]))


@qr_bp.get("/links/<int:url_id>/qr-stats")
@require_auth
@rate_limit(capacity=100, refill_rate=2)
def scan_stats(url_id):
    from app.models.event import Event

    owned_link(url_id)
    count = Event.select().where((Event.url == url_id) & (Event.event_type == "qr_scan")).count()
    return jsonify({"url_id": url_id, "recorded_scans": count})
