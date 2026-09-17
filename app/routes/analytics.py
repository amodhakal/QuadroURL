"""Per-short-code click analytics aggregation (#177).

One owner-checked endpoint rolling stored click events into time buckets,
referrers, and user-agent families. Aggregation runs in Python over a bounded
window so the SQL stays engine-agnostic (SQLite tests, Postgres prod).
Timestamps are stored naive-UTC by the shared schema (Postgres strips tz), so
bucket math treats naive values as UTC, mirroring the redirect expiry logic.
"""

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, jsonify

from app.models import Event, Url
from app.utils.auth import assert_owner, require_auth
from app.utils.ratelimit import rate_limit
from app.utils.schemas import AnalyticsQuery, parse_query

analytics_bp = Blueprint("analytics", __name__)

CLICK = "click"
BUCKET_FORMATS = {"day": "%Y-%m-%d", "week": "%G-W%V", "month": "%Y-%m"}
MAX_WINDOW_DAYS = 365

_BROWSER_PATTERNS = (
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/")),
    ("Opera", re.compile(r"OPR/|Opera")),
    ("Firefox", re.compile(r"Firefox|FxiOS")),
    ("Chrome", re.compile(r"Chrome|CriOS")),
    ("Safari", re.compile(r"Safari")),
)
_OS_PATTERNS = (
    ("iOS", re.compile(r"iPhone|iPad|iPod", re.I)),
    ("Android", re.compile(r"Android", re.I)),
    ("macOS", re.compile(r"Mac OS X|Macintosh", re.I)),
    ("Windows", re.compile(r"Windows", re.I)),
)


def classify_user_agent(user_agent):
    """Conservative local classifier; unknowns are labeled, never guessed."""
    text = user_agent or ""
    browser = next((name for name, pattern in _BROWSER_PATTERNS if pattern.search(text)), "Unknown")
    operating_system = next(
        (name for name, pattern in _OS_PATTERNS if pattern.search(text)), "Unknown"
    )
    if re.search(r"Mobile|iPhone|Android.*Mobile", text, re.I):
        device = "mobile"
    elif re.search(r"iPad|Tablet", text, re.I):
        device = "tablet"
    else:
        device = "desktop"
    return {"browser": browser, "os": operating_system, "device": device}


def _bucket_key(timestamp, bucket):
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.strftime(BUCKET_FORMATS[bucket])


def _parse_window():
    query = parse_query(AnalyticsQuery)
    return query.bucket, query.days


@analytics_bp.route("/urls/<short_code>/analytics", methods=["GET"])
@require_auth
@rate_limit(capacity=60, refill_rate=2.0)
def click_analytics(short_code):
    bucket, days = _parse_window()
    url = Url.get_or_none(Url.short_code == short_code)
    if url is None:
        abort(404)
    assert_owner(url.user_id)
    # Storage is naive-UTC; compare against a naive cutoff in UTC.
    cutoff = _cutoff(days)
    rows = (
        Event.select(Event.timestamp, Event.details)
        .where(
            Event.url == url.id,
            Event.event_type == CLICK,
            Event.timestamp >= cutoff,
        )
        .order_by(Event.timestamp)
    )

    over_time: Counter = Counter()
    referrers: Counter = Counter()
    browsers: Counter = Counter()
    operating_systems: Counter = Counter()
    devices: Counter = Counter()
    visitors = set()
    total = 0
    for event in rows:
        details = _details(event.details)
        over_time[_bucket_key(event.timestamp, bucket)] += 1
        referrers[details.get("referrer") or "direct"] += 1
        ua = details.get("user_agent") or {}
        browsers[ua.get("browser", "Unknown")] += 1
        operating_systems[ua.get("os", "Unknown")] += 1
        devices[ua.get("device", "unknown")] += 1
        if details.get("visitor"):
            visitors.add(details["visitor"])
        total += 1

    return jsonify(
        {
            "short_code": short_code,
            "bucket": bucket,
            "days": days,
            "total_clicks": total,
            "unique_visitors": len(visitors),
            "clicks_over_time": [
                {"bucket": key, "clicks": count} for key, count in sorted(over_time.items())
            ],
            "top_referrers": [
                {"referrer": key, "clicks": count} for key, count in referrers.most_common(10)
            ],
            "browsers": dict(browsers.most_common()),
            "operating_systems": dict(operating_systems.most_common()),
            "devices": dict(devices.most_common()),
        }
    )


def _cutoff(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)


def _details(raw):
    try:
        parsed = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
