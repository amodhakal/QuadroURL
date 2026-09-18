# PYTHONPATH=. uv run python scripts/backfill_embeddings.py [--batch-size 32] [--limit 0]
#
# (Re)embeds links missing an embedding row or whose title/URL changed since
# the stored content_hash. Resumable and idempotent: re-running only embeds
# the remaining gap. Requires OPENROUTER_API_KEY and a migrated database
# (migrations 004). The free tier is quota-bound — when the API answers 429,
# the script stops and reports progress so the next run continues.

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.database import db
from app.models import Url
from app.models.url_embedding import UrlEmbedding
from shared.openrouter import DEFAULT_EMBEDDING_MODEL, OpenRouterError, api_key
from shared.semantic import content_hash, embed_text


def missing_links(limit):
    from peewee import JOIN

    query = (
        Url.select(Url.id, Url.title, Url.original_url)
        .join(UrlEmbedding, JOIN.LEFT_OUTER, on=(UrlEmbedding.url == Url.id))
        .where(Url.is_active == True)  # noqa: E712
        .where(UrlEmbedding.url.is_null(True))
    )
    if limit:
        query = query.limit(limit)
    return list(query)


def changed_links(limit):
    rows = (
        Url.select(Url.id, Url.title, Url.original_url, UrlEmbedding.content_hash)
        .join(UrlEmbedding, on=(UrlEmbedding.url == Url.id))
        .where(Url.is_active == True)  # noqa: E712
    )
    if limit:
        rows = rows.limit(limit * 4)
    out = []
    for row in rows:
        if row.embedding.content_hash != content_hash(row.title, row.original_url):
            out.append(row)
        if limit and len(out) >= limit:
            break
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0, help="0 = no limit")
    args = parser.parse_args()

    key = api_key()
    if not key:
        print("OPENROUTER_API_KEY is not configured; nothing to do.")
        return 1
    model = os.environ.get("OPENROUTER_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)

    from shared.semantic import upsert_embedding

    app = create_app()
    with app.app_context():
        from shared.openrouter import embed_texts

        targets = missing_links(args.limit) + changed_links(args.limit)
        if args.limit:
            targets = targets[: args.limit]
        print(f"Backfilling {len(targets)} links with {model}")
        stored, seen = 0, 0
        batch = []
        try:
            for row in targets:
                batch.append(row)
                if len(batch) < args.batch_size:
                    continue
                vectors, _ = embed_texts(
                    [embed_text(r.title, r.original_url) for r in batch],
                    model=model,
                    input_type="search_document",
                    key=key,
                )
                for link, vector in zip(batch, vectors):
                    if upsert_embedding(
                        db,
                        UrlEmbedding,
                        link.id,
                        vector,
                        content_hash(link.title, link.original_url),
                        model,
                    ):
                        stored += 1
                seen += len(batch)
                batch = []
                print(f"  {seen}/{len(targets)} processed ({stored} stored)")
            if batch:
                vectors, _ = embed_texts(
                    [embed_text(r.title, r.original_url) for r in batch],
                    model=model,
                    input_type="search_document",
                    key=key,
                )
                for link, vector in zip(batch, vectors):
                    if upsert_embedding(
                        db,
                        UrlEmbedding,
                        link.id,
                        vector,
                        content_hash(link.title, link.original_url),
                        model,
                    ):
                        stored += 1
                seen += len(batch)
        except OpenRouterError as exc:
            print(f"Stopped after {seen}/{len(targets)} ({stored} stored): {exc}")
            print("Re-run later to continue — progress is preserved.")
            return 2 if exc.status == 429 else 1
        print(f"Done: {stored}/{len(targets)} embeddings stored.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
