"""RAG /ask contracts — validation, degraded states, answer shape, caching."""

import pytest
from peewee import PostgresqlDatabase

from shared.schema import EMBEDDING_DIM


def _require_pgvector(app):
    from app.database import db

    with app.app_context():
        if not isinstance(db.obj, PostgresqlDatabase):
            pytest.skip("requires PostgreSQL")
        cursor = db.execute_sql("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        has_vector = cursor.fetchone() is not None
        try:
            cursor.close()
        except Exception:
            pass
    if not has_vector:
        pytest.skip("requires the pgvector extension")


def _vector(first):
    vec = [0.0] * EMBEDDING_DIM
    vec[0] = first
    return vec


@pytest.fixture()
def seeded(app, seed_auth):
    _require_pgvector(app)
    from app.models.url import Url
    from app.models.url_embedding import UrlEmbedding

    with app.app_context():
        url = Url.create(
            user=seed_auth.user.id,
            short_code="ask001",
            original_url="https://example.com/pooling",
            title="Postgres pooling guide",
            is_active=True,
        )
        UrlEmbedding.create(url=url.id, embedding=_vector(1.0), content_hash="h-ask", model="test")
        return url


def test_ask_requires_auth(app):
    response = app.test_client().post("/ask", json={"question": "hi"})
    assert response.status_code == 401


def test_ask_rejects_shapes(client):
    assert client.post("/ask", json={}).status_code == 400
    assert client.post("/ask", json={"question": ""}).status_code == 400
    assert client.post("/ask", json={"question": "hi", "k": 0}).status_code == 400
    assert client.post("/ask", data="not json", content_type="application/json").status_code == 400


def test_ask_without_key_answers_503(client, seeded, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    response = client.post("/ask", json={"question": "pooling?"})
    assert response.status_code == 503


def test_ask_answers_with_cited_sources(client, seeded, monkeypatch):
    from app.routes import search as search_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(search_module, "embed_query_cached", lambda text: (_vector(1.0), False))

    def fake_chat(messages, **kwargs):
        assert any("pooling guide" in m["content"] for m in messages)
        return ("Pooling is covered by [1].", 42, 7, "qwen/qwen3.8-27b:free")

    monkeypatch.setattr(search_module, "chat_complete", fake_chat)
    response = client.post("/ask", json={"question": "How do I pool Postgres?"})
    assert response.status_code == 200
    body = response.json
    assert body["answer"] == "Pooling is covered by [1]."
    assert body["model"] == "qwen/qwen3.8-27b:free"
    assert [s["short_code"] for s in body["sources"]] == ["ask001"]


def test_ask_caches_full_answers(client, seeded, monkeypatch):
    from app.routes import search as search_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(search_module, "embed_query_cached", lambda text: (_vector(1.0), False))
    store = {}
    monkeypatch.setattr(search_module, "get_ask_cache", lambda scope, key: store.get(key))

    def fake_set(scope, key, value, ttl=600):
        store[key] = value

    monkeypatch.setattr(search_module, "set_ask_cache", fake_set)

    calls = []

    def fake_chat(messages, **kwargs):
        calls.append(messages)
        return ("Cached answer [1].", 10, 4, "qwen/qwen3.8-27b:free")

    monkeypatch.setattr(search_module, "chat_complete", fake_chat)
    first = client.post("/ask", json={"question": "pooling?"})
    second = client.post("/ask", json={"question": "pooling?"})
    assert first.status_code == 200
    assert second.json == first.json
    assert len(calls) == 1


def test_ask_reports_no_relevant_links(client, app, seed_auth, monkeypatch):
    _require_pgvector(app)
    from app.routes import search as search_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(search_module, "embed_query_cached", lambda text: (_vector(1.0), False))

    def fail(*a, **k):
        raise AssertionError("chat must not be called with zero hits")

    monkeypatch.setattr(search_module, "chat_complete", fail)
    response = client.post("/ask", json={"question": "anything?"})
    assert response.status_code == 200
    assert response.json["sources"] == []
    assert "couldn't find" in response.json["answer"]


def test_ask_chat_outage_answers_502_or_503(client, seeded, monkeypatch):
    from app.routes import search as search_module
    from shared.openrouter import OpenRouterError

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(search_module, "embed_query_cached", lambda text: (_vector(1.0), False))

    def boom(*a, **k):
        raise OpenRouterError("down", status=500, retryable=True)

    monkeypatch.setattr(search_module, "chat_complete", boom)
    assert client.post("/ask", json={"question": "pooling?"}).status_code in (502, 503)
