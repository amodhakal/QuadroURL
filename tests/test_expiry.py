"""Soft-expiry for short links (#192) + reject-past-expires_at at creation (#134).

Contract pins:

- POST /urls accepts optional ``expires_at`` (ISO 8601, tz-aware, future).
  Past dates abort 400 with a field-qualified validation error; bad formats
  and timezone-naive values abort 400 as well (naive input is rejected rather
  than silently assumed UTC).
- PUT /urls/<id> accepts ``expires_at`` (same validation; explicit null
  clears the expiry).
- Expired links redirect-404 on both ``/urls/<code>/redirect`` and
  ``/r/<code>``, mirroring inactive URLs (no new status code, no 410).
- create/get/list/status-ready payloads surface ``expires_at`` as an ISO
  string or null.
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

CONSUMER_DIR = Path(__file__).resolve().parent.parent / "consumer"

FUTURE_ISO = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
PAST_ISO = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


def _payload(sample_user, **extra):
    body = {
        "user_id": sample_user.id,
        "original_url": "https://example.com/page",
        "title": "My Page",
    }
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# POST /urls — expires_at accepted / rejected
# ---------------------------------------------------------------------------


def test_create_url_with_future_expires_at(owner_client, sample_user):
    response = owner_client.post("/urls", json=_payload(sample_user, expires_at=FUTURE_ISO))
    assert response.status_code == 201
    data = response.get_json()
    assert data["expires_at"] is not None
    parsed = datetime.fromisoformat(data["expires_at"])
    assert parsed.tzinfo is not None
    assert parsed > datetime.now(timezone.utc)

    fetched = owner_client.get(f"/urls/{data['id']}").get_json()
    assert fetched["expires_at"] == data["expires_at"]

    sample = owner_client.get("/urls").get_json()["sample"]
    by_code = {u["short_code"]: u for u in sample}
    assert by_code[data["short_code"]]["expires_at"] == data["expires_at"]


def test_create_url_expires_at_z_suffix_accepted(owner_client, sample_user):
    response = owner_client.post(
        "/urls", json=_payload(sample_user, expires_at="2030-06-01T12:00:00Z")
    )
    assert response.status_code == 201
    assert response.get_json()["expires_at"] is not None


def test_create_url_without_expires_at_defaults_null(owner_client, sample_user):
    response = owner_client.post("/urls", json=_payload(sample_user))
    assert response.status_code == 201
    assert response.get_json()["expires_at"] is None

    fetched = owner_client.get(f"/urls/{response.get_json()['id']}").get_json()
    assert fetched["expires_at"] is None


def test_create_url_past_expires_at_rejected(owner_client, sample_user):
    response = owner_client.post("/urls", json=_payload(sample_user, expires_at=PAST_ISO))
    assert response.status_code == 400
    assert (
        response.get_json()["error"] == "expires_at: Value error, expires_at must be in the future"
    )


def test_create_url_bad_format_expires_at_rejected(owner_client, sample_user):
    for bad in ("not-a-date", "", 12345, True):
        response = owner_client.post("/urls", json=_payload(sample_user, expires_at=bad))
        assert response.status_code == 400


def test_create_url_naive_expires_at_rejected(owner_client, sample_user):
    """Naive datetimes are rejected, not silently assumed UTC (#192)."""
    response = owner_client.post(
        "/urls", json=_payload(sample_user, expires_at="2030-01-01T00:00:00")
    )
    assert response.status_code == 400
    assert (
        response.get_json()["error"]
        == "expires_at: Value error, expires_at must include a timezone"
    )


# ---------------------------------------------------------------------------
# PUT /urls/<id> — expires_at update / clear / reject
# ---------------------------------------------------------------------------


def test_update_url_expires_at_set_and_cleared(owner_client, sample_url):
    response = owner_client.put(f"/urls/{sample_url.id}", json={"expires_at": FUTURE_ISO})
    assert response.status_code == 200
    assert response.get_json()["expires_at"] is not None

    cleared = owner_client.put(f"/urls/{sample_url.id}", json={"expires_at": None})
    assert cleared.status_code == 200
    assert cleared.get_json()["expires_at"] is None

    fetched = owner_client.get(f"/urls/{sample_url.id}").get_json()
    assert fetched["expires_at"] is None


def test_update_url_past_expires_at_rejected(owner_client, sample_url):
    response = owner_client.put(f"/urls/{sample_url.id}", json={"expires_at": PAST_ISO})
    assert response.status_code == 400
    assert (
        response.get_json()["error"] == "expires_at: Value error, expires_at must be in the future"
    )


def test_update_url_bad_format_expires_at_rejected(owner_client, sample_url):
    response = owner_client.put(f"/urls/{sample_url.id}", json={"expires_at": "tomorrow"})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Redirects — expired links 404 like inactive ones
# ---------------------------------------------------------------------------


def test_redirect_expired_url_404s(app, client, sample_user):
    """Expired rows (stored naive after the PG round-trip) 404 on both paths."""
    from app.models.url import Url

    with app.app_context():
        Url.create(
            user=sample_user,
            short_code="exp001",
            original_url="https://example.com/old",
            title="Expired",
            is_active=True,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )

    assert client.get("/urls/exp001/redirect").status_code == 404
    assert client.get("/r/exp001").status_code == 404
    # Repeated requests still check the fresh database lifecycle state.
    assert client.get("/urls/exp001/redirect").status_code == 404
    assert client.get("/r/exp001").status_code == 404


def test_redirect_expired_url_ignores_unexpired_cache(client, sample_url, sample_user):
    """Fresh database expiry wins over a stale, still-live short-code cache."""
    from app.cache import set_url_by_short_code

    sample_url.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    sample_url.save()
    set_url_by_short_code(
        sample_url.short_code,
        {
            "id": sample_url.id,
            "user_id": sample_user.id,
            "short_code": sample_url.short_code,
            "original_url": sample_url.original_url,
            "title": sample_url.title,
            "is_active": True,
            "expires_at": FUTURE_ISO,
        },
    )
    assert client.get(f"/urls/{sample_url.short_code}/redirect").status_code == 404
    assert client.get("/r/exp001x").status_code == 404  # unknown code still 404
    assert client.get(f"/r/{sample_url.short_code}").status_code == 404


def test_redirect_unexpired_url_unaffected(client, owner_client, sample_user):
    created = owner_client.post("/urls", json=_payload(sample_user, expires_at=FUTURE_ISO))
    assert created.status_code == 201
    short_code = created.get_json()["short_code"]

    redirect = client.get(f"/urls/{short_code}/redirect")
    assert redirect.status_code == 302

    legacy = client.get(f"/r/{short_code}")
    assert legacy.status_code == 200
    assert legacy.get_json()["url"] == "https://example.com/page"


def test_get_and_list_surface_expires_at(owner_client, sample_user):
    with_expiry = owner_client.post(
        "/urls", json=_payload(sample_user, expires_at=FUTURE_ISO)
    ).get_json()
    without_expiry = owner_client.post("/urls", json=_payload(sample_user)).get_json()

    assert owner_client.get(f"/urls/{with_expiry['id']}").get_json()["expires_at"] is not None
    assert owner_client.get(f"/urls/{without_expiry['id']}").get_json()["expires_at"] is None

    sample = owner_client.get("/urls").get_json()["sample"]
    by_code = {u["short_code"]: u for u in sample}
    assert "expires_at" in by_code[with_expiry["short_code"]]
    assert "expires_at" in by_code[without_expiry["short_code"]]
    assert by_code[without_expiry["short_code"]]["expires_at"] is None


# ---------------------------------------------------------------------------
# Consumer — expires_at persists through the async create path
# ---------------------------------------------------------------------------


def _load_handler(monkeypatch):
    monkeypatch.syspath_prepend(str(CONSUMER_DIR))
    saved = sys.modules.get("url_create_handler", None)
    marker = object()
    had = saved if saved is not None else marker
    try:
        spec = importlib.util.spec_from_file_location(
            "url_create_handler", str(CONSUMER_DIR / "url_create_handler.py")
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["url_create_handler"] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        if had is marker:
            sys.modules.pop("url_create_handler", None)
        else:
            sys.modules["url_create_handler"] = had
        raise


def _teardown_handler(saved_exists, saved):
    if not saved_exists:
        sys.modules.pop("url_create_handler", None)
    else:
        sys.modules["url_create_handler"] = saved


def test_consumer_batch_persists_expires_at(monkeypatch):
    saved_exists = "url_create_handler" in sys.modules
    saved = sys.modules.get("url_create_handler")
    handler = _load_handler(monkeypatch)
    try:
        expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        created = SimpleNamespace(
            id=9,
            user_id=7,
            short_code="Ex9p1r",
            original_url="https://example.com/x",
            title="X",
            expires_at=datetime.fromisoformat(expiry),
        )
        create_calls = []

        def fake_create(**kwargs):
            create_calls.append(kwargs)
            return created

        monkeypatch.setattr(handler.Url, "create", fake_create)
        db = MagicMock(name="fake_db")
        redis_client = MagicMock(name="redis_client")
        pipe = redis_client.pipeline.return_value

        ok, events = handler.handle_url_create_batch(
            [
                {
                    "request_id": "r-exp",
                    "user_id": 7,
                    "original_url": "https://example.com/x",
                    "title": "X",
                    "expires_at": expiry,
                }
            ],
            db,
            redis_client,
        )

        assert ok is True
        assert len(events) == 1
        assert create_calls and "expires_at" in create_calls[0]
        stored = create_calls[0]["expires_at"]
        assert isinstance(stored, datetime) and stored.tzinfo is not None
        _, _, raw = pipe.setex.call_args.args
        payload = json.loads(raw)
        assert payload["status"] == "ready"
        assert payload["expires_at"] == expiry
    finally:
        _teardown_handler(saved_exists, saved)
        for name in ("config", "consumer_app"):
            sys.modules.pop(name, None)


def test_consumer_batch_without_expires_at_omits_kwarg(monkeypatch):
    """Messages without expiry keep the original six-kwarg create contract."""
    saved_exists = "url_create_handler" in sys.modules
    saved = sys.modules.get("url_create_handler")
    handler = _load_handler(monkeypatch)
    try:
        created = SimpleNamespace(
            id=10,
            user_id=7,
            short_code="NoExp1",
            original_url="https://example.com/y",
            title="Y",
        )
        create_calls = []

        def fake_create(**kwargs):
            create_calls.append(kwargs)
            return created

        monkeypatch.setattr(handler.Url, "create", fake_create)
        db = MagicMock(name="fake_db")
        redis_client = MagicMock(name="redis_client")

        ok, _ = handler.handle_url_create_batch(
            [
                {
                    "request_id": "r-noexp",
                    "user_id": 7,
                    "original_url": "https://example.com/y",
                    "title": "Y",
                }
            ],
            db,
            redis_client,
        )

        assert ok is True
        assert create_calls and set(create_calls[0]) == {
            "user_id",
            "short_code",
            "original_url",
            "title",
            "is_active",
            "request_id",
        }
    finally:
        _teardown_handler(saved_exists, saved)
        for name in ("config", "consumer_app"):
            sys.modules.pop(name, None)
