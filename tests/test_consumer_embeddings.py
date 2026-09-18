"""Consumer ingest embeds links best-effort — hermetic SQLite, HTTP mocked."""

import importlib
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from peewee import SqliteDatabase

from shared.schema import EMBEDDING_DIM


@pytest.fixture(autouse=True)
def clean_tables():
    yield  # hermetic SQLite, no integration DB needed


@contextmanager
def _load_consumer(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "consumer"))
    names = ("models", "config", "url_create_handler")
    saved = {name: sys.modules.get(name) for name in names}
    try:
        consumer = importlib.import_module("models")
        handler = importlib.import_module("url_create_handler")
        database = SqliteDatabase(str(tmp_path / "consumer_emb.db"), pragmas={"foreign_keys": 1})
        tables = [consumer.User, consumer.Url, consumer.UrlEmbedding]
        with database:
            # create_tables follows each model's bound database, so bind the
            # consumer models to SQLite first (mirrors test_shared_schema).
            with database.bind_ctx(tables):
                database.create_tables(tables)
                user = consumer.User.create(username="cemb", email="cemb@example.com")
            yield consumer, handler, database, user
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_batch_embeds_new_links(monkeypatch, tmp_path):
    from shared import openrouter as or_module

    with _load_consumer(monkeypatch, tmp_path) as (consumer, handler, database, user):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        def fake_embed(texts, **kwargs):
            assert kwargs.get("input_type") == "search_document"
            return [[0.5] * EMBEDDING_DIM for _ in texts], 9

        monkeypatch.setattr(or_module, "embed_texts", fake_embed)
        with database.bind_ctx([consumer.User, consumer.Url, consumer.UrlEmbedding]):
            ok, events = handler.handle_url_create_batch(
                [
                    {
                        "request_id": "emb-1",
                        "user_id": user.id,
                        "original_url": "https://example.com/a",
                        "title": "Alpha",
                    }
                ],
                database,
                MagicMock(),
            )
            assert ok and len(events) == 1
            row = consumer.UrlEmbedding.get(consumer.UrlEmbedding.url == events[0]["url_id"])
            assert len(row.embedding) == EMBEDDING_DIM
            assert row.embedding[0] == 0.5


def test_batch_survives_embedding_outage(monkeypatch, tmp_path):
    from shared import openrouter as or_module
    from shared.openrouter import OpenRouterError

    with _load_consumer(monkeypatch, tmp_path) as (consumer, handler, database, user):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        def boom(*a, **k):
            raise OpenRouterError("quota", status=429, retryable=True)

        monkeypatch.setattr(or_module, "embed_texts", boom)
        with database.bind_ctx([consumer.User, consumer.Url, consumer.UrlEmbedding]):
            ok, events = handler.handle_url_create_batch(
                [
                    {
                        "request_id": "emb-2",
                        "user_id": user.id,
                        "original_url": "https://example.com/b",
                        "title": "Beta",
                    }
                ],
                database,
                MagicMock(),
            )
            # Creation succeeds; only the embedding row is missing.
            assert ok and len(events) == 1
            assert consumer.UrlEmbedding.select().count() == 0


def test_batch_skips_embed_without_key(monkeypatch, tmp_path):
    from shared import openrouter as or_module

    with _load_consumer(monkeypatch, tmp_path) as (consumer, handler, database, user):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        def fail(*a, **k):
            raise AssertionError("no HTTP without a key")

        monkeypatch.setattr(or_module, "embed_texts", fail)
        with database.bind_ctx([consumer.User, consumer.Url, consumer.UrlEmbedding]):
            ok, events = handler.handle_url_create_batch(
                [
                    {
                        "request_id": "emb-3",
                        "user_id": user.id,
                        "original_url": "https://example.com/c",
                        "title": "Gamma",
                    }
                ],
                database,
                MagicMock(),
            )
            assert ok and len(events) == 1
            assert consumer.UrlEmbedding.select().count() == 0
