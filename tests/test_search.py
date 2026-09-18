"""Semantic /search contracts — validation, scoping, and pgvector ranking."""

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


def _vector(first, second=0.0):
    vec = [0.0] * EMBEDDING_DIM
    vec[0] = first
    vec[1] = second
    return vec


@pytest.fixture()
def seeded(app, seed_auth):
    """Two owned links with known vectors: near=[1,0,...], far=[0,1,...]."""
    _require_pgvector(app)
    from app.models.url import Url
    from app.models.url_embedding import UrlEmbedding

    with app.app_context():
        near = Url.create(
            user=seed_auth.user.id,
            short_code="near01",
            original_url="https://example.com/pooling",
            title="Postgres pooling guide",
            is_active=True,
        )
        UrlEmbedding.create(
            url=near.id, embedding=_vector(1.0), content_hash="h-near", model="test"
        )
        far = Url.create(
            user=seed_auth.user.id,
            short_code="far002",
            original_url="https://example.com/baking",
            title="Sourdough baking guide",
            is_active=True,
        )
        UrlEmbedding.create(
            url=far.id, embedding=_vector(0.0, 1.0), content_hash="h-far", model="test"
        )
        return near, far


def test_search_requires_auth(app):
    response = app.test_client().get("/search?q=hello")
    assert response.status_code == 401


def test_search_rejects_shapes(client):
    assert client.get("/search").status_code == 400
    assert client.get("/search?q=").status_code == 400
    assert client.get("/search?q=hi&k=0").status_code == 400
    assert client.get("/search?q=hi&k=21").status_code == 400
    assert client.get("/search?q=hi&k=nope").status_code == 400
    assert client.get("/api/v1/search").status_code == 400


def test_search_without_key_answers_503(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    response = client.get("/search?q=pooling")
    assert response.status_code == 503


def test_search_ranks_by_cosine_similarity(client, seeded, monkeypatch):
    from app.routes import search as search_module

    monkeypatch.setattr(search_module, "embed_query_cached", lambda text: (_vector(1.0), False))
    response = client.get("/search?q=pooling&k=2")
    assert response.status_code == 200
    body = response.json
    assert body["kind"] == "search"
    assert body["query"] == "pooling"
    assert [hit["short_code"] for hit in body["results"]] == ["near01", "far002"]
    assert body["results"][0]["score"] == pytest.approx(1.0)
    assert body["results"][0]["original_url"] == "https://example.com/pooling"


def test_search_is_owner_scoped(client, app, seed_auth, seeded, monkeypatch):
    from app.models.url import Url
    from app.models.url_embedding import UrlEmbedding
    from app.routes import search as search_module

    with app.app_context():
        from app.models.user import User

        stranger = User.create(username="stranger", email="stranger@example.com")
        other = Url.create(
            user=stranger.id,
            short_code="other1",
            original_url="https://example.com/other",
            title="Other guide",
            is_active=True,
        )
        UrlEmbedding.create(
            url=other.id, embedding=_vector(1.0), content_hash="h-other", model="test"
        )
    monkeypatch.setattr(search_module, "embed_query_cached", lambda text: (_vector(1.0), False))
    codes = [hit["short_code"] for hit in client.get("/search?q=guide&k=10").json["results"]]
    assert "other1" not in codes
    assert "near01" in codes


def test_search_skips_inactive_links(client, app, seeded, monkeypatch):
    from app.models.url import Url
    from app.routes import search as search_module

    with app.app_context():
        Url.update(is_active=False).where(Url.short_code == "near01").execute()
    monkeypatch.setattr(search_module, "embed_query_cached", lambda text: (_vector(1.0), False))
    codes = [hit["short_code"] for hit in client.get("/search?q=pooling&k=2").json["results"]]
    assert "near01" not in codes
    assert codes == ["far002"]


def test_search_uses_cached_query_embedding(client, seeded, monkeypatch):
    import json as _json

    from app import cache
    from app.utils.retrieval import embed_query_cached
    from shared import openrouter as or_module

    # Stub the L2 read with a cached near-vector payload.
    monkeypatch.setattr(cache, "_l2_safe", lambda fn: _json.dumps({"v": _vector(1.0), "t": 3}))

    def fail(*a, **k):
        raise AssertionError("embedding API must not be called on cache hit")

    monkeypatch.setattr(or_module.requests, "post", fail)
    vector, cached = embed_query_cached("pooling")
    assert cached is True
    assert vector[0] == 1.0
