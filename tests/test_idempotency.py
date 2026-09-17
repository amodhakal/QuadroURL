"""Idempotency tests for POST /urls (#113).

With KAFKA_SYNC_FALLBACK=1 (set by conftest) POST /urls creates rows
synchronously; the ``Idempotency-Key`` header (or ``request_id`` JSON field)
must deduplicate client retries to a single row.
"""

from hashlib import sha256

from app.models.url import Url


def _payload(sample_user, **extra):
    body = {
        "user_id": sample_user.id,
        "original_url": "https://example.com/page",
        "title": "My Page",
    }
    body.update(extra)
    return body


def test_same_key_double_post_creates_one_row(owner_client, sample_user):
    headers = {"Idempotency-Key": "idem-key-1"}
    first = owner_client.post("/urls", json=_payload(sample_user), headers=headers)
    assert first.status_code == 201

    second = owner_client.post("/urls", json=_payload(sample_user), headers=headers)
    assert second.status_code == 200
    assert second.get_json()["id"] == first.get_json()["id"]
    assert Url.select().count() == 1
    assert Url.get_by_id(first.get_json()["id"]).request_id == (
        f"u{sample_user.id}:" + sha256(b"idem-key-1").hexdigest()
    )


def test_same_key_via_body_request_id_dedupes(owner_client, sample_user):
    first = owner_client.post("/urls", json=_payload(sample_user, request_id="body-key-1"))
    assert first.status_code == 201

    second = owner_client.post("/urls", json=_payload(sample_user, request_id="body-key-1"))
    assert second.status_code == 200
    assert second.get_json()["id"] == first.get_json()["id"]
    assert Url.select().count() == 1


def test_header_wins_over_body_request_id(owner_client, sample_user):
    body = _payload(sample_user, request_id="body-key-loser")
    first = owner_client.post("/urls", json=body, headers={"Idempotency-Key": "header-key-wins"})
    assert first.status_code == 201
    assert Url.get_by_id(first.get_json()["id"]).request_id == (
        f"u{sample_user.id}:" + sha256(b"header-key-wins").hexdigest()
    )

    second = owner_client.post("/urls", json=body, headers={"Idempotency-Key": "header-key-wins"})
    assert second.status_code == 200
    assert second.get_json()["id"] == first.get_json()["id"]
    assert Url.select().count() == 1


def test_empty_key_is_ignored(owner_client, sample_user):
    first = owner_client.post("/urls", json=_payload(sample_user), headers={"Idempotency-Key": ""})
    assert first.status_code == 201
    second = owner_client.post(
        "/urls", json=_payload(sample_user), headers={"Idempotency-Key": "   "}
    )
    assert second.status_code == 201
    assert second.get_json()["id"] != first.get_json()["id"]
    assert Url.select().count() == 2


def test_different_keys_create_two_rows(owner_client, sample_user):
    r1 = owner_client.post(
        "/urls", json=_payload(sample_user), headers={"Idempotency-Key": "key-a"}
    )
    r2 = owner_client.post(
        "/urls", json=_payload(sample_user), headers={"Idempotency-Key": "key-b"}
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.get_json()["id"] != r2.get_json()["id"]
    assert Url.select().count() == 2


def test_no_key_creates_two_rows(owner_client, sample_user):
    r1 = owner_client.post("/urls", json=_payload(sample_user))
    r2 = owner_client.post("/urls", json=_payload(sample_user))
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.get_json()["id"] != r2.get_json()["id"]
    assert Url.select().count() == 2


def test_status_flow_unaffected(owner_client, sample_user):
    """Plain creates keep working and unknown status keys still 404."""
    created = owner_client.post("/urls", json=_payload(sample_user))
    assert created.status_code == 201

    missing = owner_client.get("/urls/does-not-exist/status")
    assert missing.status_code == 404


def test_same_key_is_scoped_to_actor(client, owner_client, sample_user, seed_auth):
    headers = {"Idempotency-Key": "shared-key"}
    owned = owner_client.post("/urls", json=_payload(sample_user), headers=headers)
    foreign = client.post("/urls", json=_payload(seed_auth.user), headers=headers)
    assert owned.status_code == foreign.status_code == 201
    assert owned.get_json()["id"] != foreign.get_json()["id"]
    assert Url.select().count() == 2

    for actor_client, user, original in (
        (owner_client, sample_user, owned),
        (client, seed_auth.user, foreign),
    ):
        replay = actor_client.post("/urls", json=_payload(user), headers=headers)
        assert replay.status_code == 200
        assert replay.get_json()["id"] == original.get_json()["id"]
        assert replay.get_json()["user_id"] == user.id

    request_id = Url.get_by_id(owned.get_json()["id"]).request_id
    assert client.get(f"/urls/{request_id}/status").status_code == 404
