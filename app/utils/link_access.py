"""One public-access policy for redirects, expansion and QR scan routes."""

from datetime import datetime, timezone

from flask import abort, make_response, request
from werkzeug.security import check_password_hash

from app.database import models
from app.models.url import Url
from app.utils.ratelimit import rate_limit
from app.utils.url_safety import require_destination_safe

_PASSWORD_FORM = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Password required</title><h1>Password required</h1>
<form method="post"><label>Link password <input name="password" type="password"
required minlength="8" maxlength="128" autocomplete="current-password"></label>
<button type="submit">Continue</button></form></html>"""


def _password_budget():
    from app.utils.request_ctx import get_client_ip

    # Shared across every alias/code so switching endpoints doesn't reset budget.
    return f"link-password:{get_client_ip()}"


@rate_limit(capacity=10, refill_rate=0.1, key_func=_password_budget)
def _check_password(password_hash):
    password = request.headers.get("X-Link-Password", "")
    if request.method == "POST":
        data = request.get_json(silent=True) if request.is_json else request.form
        if isinstance(data, dict) or hasattr(data, "get"):
            password = data.get("password", "")
    if (
        not isinstance(password, str)
        or not 8 <= len(password) <= 128
        or not check_password_hash(password_hash, password)
    ):
        if request.accept_mimetypes.best == "text/html":
            response = make_response(_PASSWORD_FORM, 401)
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
            )
            abort(response)
        abort(401, description="Link password required or incorrect")
    return ""  # Decorator returns a Response; caller only needs raised errors.


def resolve_public_link(short_code):
    from app.routes.urls import _is_expired, format_url

    # Security state always comes from the DB. Never fall back to a stale cache
    # on database failure, and never leak a password/hash via a cached payload.
    link = Url.get_or_none(Url.short_code == short_code)
    if (
        link is None
        or not link.is_active
        or _is_expired(link.expires_at, datetime.now(timezone.utc))
    ):
        abort(404)
    metadata = models.LinkMetadata.get_or_none(models.LinkMetadata.url == link.id)
    if metadata and metadata.password_hash:
        response = make_response(_check_password(metadata.password_hash))
        # Rate limiter returns a tuple/Response instead of raising.
        if response.status_code != 200:
            abort(response)
    require_destination_safe(link.original_url)
    return format_url(link)


def private_response(response):
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
