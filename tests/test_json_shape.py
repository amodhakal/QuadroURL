"""Regression tests for issue #240.

Truthy non-dict JSON bodies (e.g. ``"hello"``, ``[1,2]``, ``42``, ``true``)
must abort 400 ``Invalid JSON`` via ``require_json`` instead of crashing
with ``AttributeError`` on ``data.get`` (500).
"""

import pytest

BODIES = ['"hello"', "[1,2]", "42", "true"]


@pytest.mark.parametrize("body", BODIES)
def test_create_user_rejects_non_dict_json(client, body):
    r = client.post("/users", data=body, content_type="application/json")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Invalid JSON"


@pytest.mark.parametrize("body", BODIES)
def test_update_user_rejects_non_dict_json(client, sample_user, body):
    r = client.put(f"/users/{sample_user.id}", data=body, content_type="application/json")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Invalid JSON"


@pytest.mark.parametrize("body", BODIES)
def test_create_url_rejects_non_dict_json(client, body):
    r = client.post("/urls", data=body, content_type="application/json")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Invalid JSON"


@pytest.mark.parametrize("body", BODIES)
def test_update_url_rejects_non_dict_json(client, sample_url, body):
    r = client.put(f"/urls/{sample_url.id}", data=body, content_type="application/json")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Invalid JSON"


@pytest.mark.parametrize("body", BODIES)
def test_create_event_rejects_non_dict_json(client, body):
    r = client.post("/events", data=body, content_type="application/json")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Invalid JSON"
