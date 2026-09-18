"""Semantic retrieval over link embeddings (pgvector cosine similarity).

Embed text v1 is ``title`` + ``original_url`` — the only content captured at
ingest. Query-time flow: embed the question (Redis-cached) -> top-k cosine
lookup scoped to the caller's own links (admins see all, mirroring
``scope_query``). All external calls are wrapped in Prometheus metrics; the
free OpenRouter tier is quota-bound, so latency/token/failure signals are
first-class.
"""

import hashlib
import logging
import os
import time

from shared.semantic import content_hash, embed_text

logger = logging.getLogger("quadroPE.retrieval")

EMBEDDING_DIM_EXPECTED = 1024


def embedding_model():
    return (
        os.environ.get("OPENROUTER_EMBEDDING_MODEL") or "liquid/lfm-2.5-embedding-350m:free"
    ).strip()


def chat_model():
    return (os.environ.get("OPENROUTER_CHAT_MODEL") or "qwen/qwen3.8-27b:free").strip()


def chat_fallback_model():
    return (os.environ.get("OPENROUTER_FALLBACK_MODEL") or "openrouter/free").strip()


def ask_cache_ttl():
    try:
        return max(0, int(os.environ.get("ASK_CACHE_TTL", 600)))
    except (TypeError, ValueError):
        return 600


__all__ = ["content_hash", "embed_text"]


def query_cache_key(model, text):
    return hashlib.sha256(f"{model}|search_query|{text}".encode("utf-8")).hexdigest()


def _vector_literal(vector):
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def embed_query_cached(text):
    """Embed one query string, reusing the Redis embedding cache.

    Returns ``(vector, cached_bool)``. Raises :class:`OpenRouterError` when
    the embedding service is unavailable so routes can answer 503.
    """
    from app.cache import get_cached_embedding, set_cached_embedding
    from app.routes.prometheus import (
        EMBEDDING_DURATION,
        EMBEDDING_REQUESTS,
        EMBEDDING_TOKENS,
        SEMANTIC_CACHE_HITS,
    )
    from shared.openrouter import OpenRouterError, embed_texts

    model = embedding_model()
    key = query_cache_key(model, text)
    hit = get_cached_embedding(key)
    if isinstance(hit, dict) and isinstance(hit.get("v"), list):
        SEMANTIC_CACHE_HITS.labels(cache="embedding", outcome="hit").inc()
        return hit["v"], True
    SEMANTIC_CACHE_HITS.labels(cache="embedding", outcome="miss").inc()

    start = time.perf_counter()
    try:
        vectors, tokens = embed_texts([text], model=model, input_type="search_query")
    except OpenRouterError:
        EMBEDDING_REQUESTS.labels(operation="query", status="error").inc()
        EMBEDDING_DURATION.labels(operation="query").observe(time.perf_counter() - start)
        raise
    EMBEDDING_REQUESTS.labels(operation="query", status="ok").inc()
    EMBEDDING_DURATION.labels(operation="query").observe(time.perf_counter() - start)
    EMBEDDING_TOKENS.labels(operation="query").inc(tokens)
    vector = vectors[0]
    if len(vector) != EMBEDDING_DIM_EXPECTED:
        raise OpenRouterError(
            f"Embedding dim {len(vector)} != {EMBEDDING_DIM_EXPECTED} "
            f"(model {model} drifted; migration 004 assumed 1024)"
        )
    set_cached_embedding(key, vector, tokens)
    return vector, False


def semantic_search(vector, *, user_id=None, is_admin=False, k=5):
    """Top-k active links by cosine similarity. Returns rows with ``score``.

    ``score`` is ``1 - cosine_distance`` (1.0 = identical). Raises
    ``RuntimeError`` outside Postgres (SQLite has no pgvector operators).
    """
    from app.database import db
    from app.routes.prometheus import RETRIEVAL_DURATION
    from peewee import PostgresqlDatabase

    start = time.perf_counter()
    try:
        if not isinstance(db.obj, PostgresqlDatabase):
            raise RuntimeError("Semantic search requires PostgreSQL + pgvector")
        literal = _vector_literal(vector)
        args = [literal]
        scope = ""
        if not is_admin:
            scope = "AND u.user_id = %s"
            args.append(user_id)
        args.extend([literal, k])
        cursor = db.execute_sql(
            "SELECT u.id, u.user_id, u.short_code, u.original_url, u.title, "
            "(e.embedding <=> %s::vector) AS distance "
            'FROM "url" u JOIN "urlembedding" e ON e.url_id = u.id '
            "WHERE u.is_active = TRUE " + scope + " "
            "ORDER BY e.embedding <=> %s::vector LIMIT %s",
            args,
        )
        rows = cursor.fetchall()
        try:
            cursor.close()
        except Exception:
            pass
        return [
            {
                "id": row[0],
                "user_id": row[1],
                "short_code": row[2],
                "original_url": row[3],
                "title": row[4],
                "score": round(1.0 - float(row[5]), 4),
            }
            for row in rows
        ]
    finally:
        RETRIEVAL_DURATION.labels(operation="search").observe(time.perf_counter() - start)


def build_ask_context(hits):
    lines = []
    for i, hit in enumerate(hits, 1):
        lines.append(
            f"[{i}] {hit['title']} — {hit['original_url']} (short_code: {hit['short_code']})"
        )
    return "\n".join(lines)


ASK_SYSTEM_PROMPT = (
    "You answer questions using ONLY the provided saved-link context. "
    "Cite sources inline as [1], [2], etc. matching the numbered links. "
    "If the context cannot answer the question, say so plainly instead of "
    "inventing an answer."
)
