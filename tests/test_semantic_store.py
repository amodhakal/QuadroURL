"""Semantic ingest-store contracts — hermetic SQLite, no services."""

import pytest
from peewee import SqliteDatabase

from shared.schema import EMBEDDING_DIM, create_models
from shared.semantic import content_hash, embed_links_batch, embed_text, upsert_embedding


@pytest.fixture(autouse=True)
def clean_tables():
    yield  # hermetic SQLite, no integration DB needed


@pytest.fixture()
def models():
    database = SqliteDatabase(":memory:")
    namespace = create_models(database)
    with database:
        database.create_tables([namespace.User, namespace.Url, namespace.UrlEmbedding])
        yield namespace


@pytest.fixture()
def link(models):
    user = models.User.create(username="emb", email="emb@example.com")
    return models.Url.create(
        user_id=user.id,
        short_code="emb001",
        original_url="https://example.com/pooling",
        title="Postgres pooling guide",
    )


def _vector(first=1.0):
    return [first] + [0.0] * (EMBEDDING_DIM - 1)


def test_embed_text_and_hash_are_deterministic():
    assert embed_text("T", "https://example.com") == "T\nhttps://example.com"
    assert content_hash("T", "https://example.com") == content_hash("T", "https://example.com")
    assert content_hash("T", "https://example.com/x") != content_hash("T", "https://example.com")


def test_upsert_inserts_then_updates(models, link):
    db = models.Url._meta.database
    assert upsert_embedding(db, models.UrlEmbedding, link.id, _vector(), "h1", "m") is True
    row = models.UrlEmbedding.get(models.UrlEmbedding.url == link.id)
    assert row.content_hash == "h1"
    assert row.model == "m"
    assert len(row.embedding) == EMBEDDING_DIM

    assert upsert_embedding(db, models.UrlEmbedding, link.id, _vector(0.5), "h2", "m") is True
    row = models.UrlEmbedding.get(models.UrlEmbedding.url == link.id)
    assert row.content_hash == "h2"
    assert row.embedding[0] == 0.5
    assert models.UrlEmbedding.select().count() == 1


def test_upsert_rejects_wrong_dim(models, link):
    db = models.Url._meta.database
    assert upsert_embedding(db, models.UrlEmbedding, link.id, [1.0, 2.0], "h", "m") is False
    assert models.UrlEmbedding.select().count() == 0


def test_batch_skips_without_key(models, link):
    db = models.Url._meta.database
    stored = embed_links_batch(
        db, models.UrlEmbedding, [(link.id, link.title, link.original_url)], model="m", key=""
    )
    assert stored == 0
    assert models.UrlEmbedding.select().count() == 0


def test_batch_skips_unchanged_content(models, link, monkeypatch):
    from shared import openrouter as or_module

    db = models.Url._meta.database
    digest = content_hash(link.title, link.original_url)
    models.UrlEmbedding.create(url=link.id, embedding=_vector(), content_hash=digest, model="m")

    def fail(*a, **k):
        raise AssertionError("API must not be called for unchanged content")

    monkeypatch.setattr(or_module, "embed_texts", fail)
    stored = embed_links_batch(
        db, models.UrlEmbedding, [(link.id, link.title, link.original_url)], model="m", key="k"
    )
    assert stored == 0


def test_batch_embeds_changed_content(models, link, monkeypatch):
    from shared import openrouter as or_module

    db = models.Url._meta.database

    def fake_embed(texts, **kwargs):
        assert kwargs.get("input_type") == "search_document"
        return [[0.25] * EMBEDDING_DIM for _ in texts], 7

    monkeypatch.setattr(or_module, "embed_texts", fake_embed)
    stored = embed_links_batch(
        db, models.UrlEmbedding, [(link.id, link.title, link.original_url)], model="m", key="k"
    )
    assert stored == 1
    row = models.UrlEmbedding.get(models.UrlEmbedding.url == link.id)
    assert row.content_hash == content_hash(link.title, link.original_url)
