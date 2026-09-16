"""Idempotency tests for POST /urls (#113).

With KAFKA_SYNC_FALLBACK=1 (set by conftest) POST /urls creates rows
synchronously; the ``Idempotency-Key`` header (or ``request_id`` JSON field)
must deduplicate client retries to a single row.
"""

from app.models.url import Url


def _payload(sample_user, **extra):
    body = {
        "user_id": sample_user.id,
        "original_url": "https://example.com/page",
        "title": "My Page",
    }
    body.update(extra)
    return body


def test_same_key_double_post_creates_one_row(client, sample_user):
    headers = {"Idempotency-Key": "idem-key-1"}
    first = client.post("/urls", json=_payload(sample_user), headers=headers)
    assert first.status_code == 201

    second = client.post("/urls", json=_payload(sample_user), headers=headers)
    assert second.status_code == 200
    assert second.get_json()["id"] == first.get_json()["id"]
    assert Url.select().count() == 1
    assert Url.get_by_id(first.get_json()["id"]).request_id == "idem-key-1"


def test_same_key_via_body_request_id_dedupes(client, sample_user):
    first = client.post("/urls", json=_payload(sample_user, request_id="body-key-1"))
    assert first.status_code == 201

    second = client.post("/urls", json=_payload(sample_user, request_id="body-key-1"))
    assert second.status_code == 200
    assert second.get_json()["id"] == first.get_json()["id"]
    assert Url.select().count() == 1


def test_header_wins_over_body_request_id(client, sample_user):
    body = _payload(sample_user, request_id="body-key-loser")
    first = client.post("/urls", json=body, headers={"Idempotency-Key": "header-key-wins"})
    assert first.status_code == 201
    assert Url.get_by_id(first.get_json()["id"]).request_id == "header-key-wins"

    second = client.post("/urls", json=body, headers={"Idempotency-Key": "header-key-wins"})
    assert second.status_code == 200
    assert second.get_json()["id"] == first.get_json()["id"]
    assert Url.select().count() == 1


def test_empty_key_is_ignored(client, sample_user):
    first = client.post("/urls", json=_payload(sample_user), headers={"Idempotency-Key": ""})
    assert first.status_code == 201
    second = client.post("/urls", json=_payload(sample_user), headers={"Idempotency-Key": "   "})
    assert second.status_code == 201
    assert second.get_json()["id"] != first.get_json()["id"]
    assert Url.select().count() == 2


def test_different_keys_create_two_rows(client, sample_user):
    r1 = client.post("/urls", json=_payload(sample_user), headers={"Idempotency-Key": "key-a"})
    r2 = client.post("/urls", json=_payload(sample_user), headers={"Idempotency-Key": "key-b"})
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.get_json()["id"] != r2.get_json()["id"]
    assert Url.select().count() == 2


def test_no_key_creates_two_rows(client, sample_user):
    r1 = client.post("/urls", json=_payload(sample_user))
    r2 = client.post("/urls", json=_payload(sample_user))
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.get_json()["id"] != r2.get_json()["id"]
    assert Url.select().count() == 2


def test_status_flow_unaffected(client, sample_user):
    """Plain creates keep working and unknown status keys still 404."""
    created = client.post("/urls", json=_payload(sample_user))
    assert created.status_code == 201

    missing = client.get("/urls/does-not-exist/status")
    assert missing.status_code in (404, 503)
