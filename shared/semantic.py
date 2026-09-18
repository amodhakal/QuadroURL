"""Shared semantic-layer helpers for ingest (API sync fallback + consumers).

Only depends on ``shared.schema`` / ``shared.openrouter`` plus peewee, so both
the Flask app and the standalone Kafka consumers can use it. Every function
here is fail-open by contract: embedding must never break URL creation.
Storage goes through the Peewee ``UrlEmbedding`` model so the same code runs
on PostgreSQL (pgvector) and SQLite (hermetic tests); similarity SQL stays
Postgres-only next to the /search route.
"""

import hashlib
import logging
from datetime import datetime, timezone

logger = logging.getLogger("quadroPE.semantic")


def embed_text(title, original_url):
    """Deterministic embed input for one link (ingest, backfill, re-embed)."""
    return f"{title or ''}\n{original_url or ''}".strip()


def content_hash(title, original_url):
    return hashlib.sha256(embed_text(title, original_url).encode("utf-8")).hexdigest()


def upsert_embedding(database, url_embedding_model, url_id, vector, digest, model):
    """Store one embedding row; insert-or-update by URL. Never raises."""
    from peewee import IntegrityError

    from shared.schema import EMBEDDING_DIM

    try:
        if not isinstance(vector, (list, tuple)) or len(vector) != EMBEDDING_DIM:
            logger.warning(
                f"Skipping embedding for url_id={url_id}: "
                f"dim {len(vector) if isinstance(vector, (list, tuple)) else '?'} "
                f"!= {EMBEDDING_DIM}"
            )
            return False
        now = datetime.now(timezone.utc)
        try:
            url_embedding_model.create(
                url=url_id,
                embedding=list(vector),
                content_hash=digest,
                model=model,
                updated_at=now,
            )
        except IntegrityError:
            (
                url_embedding_model.update(
                    embedding=list(vector),
                    content_hash=digest,
                    model=model,
                    updated_at=now,
                )
                .where(url_embedding_model.url == url_id)
                .execute()
            )
        return True
    except Exception:
        logger.warning(f"Embedding store failed for url_id={url_id}", exc_info=True)
        return False


def embed_links_batch(database, url_embedding_model, links, *, model, key, timeout=30):
    """Embed ``[(url_id, title, original_url)]`` in one API call and store rows.

    Skips links whose ``content_hash`` already matches the stored row.
    No API key (or any failure) degrades to 0 stored embeddings — callers treat
    this as best-effort. Returns the number of rows stored.
    """
    from shared.openrouter import embed_texts

    if not links or not key:
        return 0
    try:
        try:
            digests = {
                row.url_id: row.content_hash
                for row in url_embedding_model.select(
                    url_embedding_model.url, url_embedding_model.content_hash
                ).where(url_embedding_model.url.in_([url_id for url_id, _, _ in links]))
            }
        except Exception:
            # Missing table (pre-migration DB) or backend hiccup: the whole
            # batch degrades instead of failing creation.
            logger.warning("Embedding lookup failed; skipping batch embed")
            return 0
        pending = [
            (url_id, content_hash(title, original_url), embed_text(title, original_url))
            for url_id, title, original_url in links
            if digests.get(url_id) != content_hash(title, original_url)
        ]
        if not pending:
            return 0
        vectors, _ = embed_texts(
            [text for _, _, text in pending],
            model=model,
            input_type="search_document",
            key=key,
            timeout=timeout,
        )
        stored = 0
        for (url_id, digest, _), vector in zip(pending, vectors):
            if upsert_embedding(database, url_embedding_model, url_id, vector, digest, model):
                stored += 1
        return stored
    except Exception:
        logger.warning("Batch embed failed (ingest unaffected)", exc_info=True)
        return 0
