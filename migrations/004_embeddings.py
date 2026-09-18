"""Semantic-search embeddings for shortened links (pgvector, 1024-dim).

Creates the ``urlembedding`` table (one row per URL, cascade on delete) and,
on PostgreSQL only, enables the ``vector`` extension plus an HNSW cosine index
for top-k similarity lookup. SQLite accepts the ``vector(1024)`` column type
name verbatim, so hermetic unit tests keep working; similarity queries are
Postgres-only and live in raw SQL next to the /search route.
"""

from peewee import PostgresqlDatabase, SqliteDatabase


def upgrade(database, models):
    if isinstance(database, PostgresqlDatabase):
        database.execute_sql("CREATE EXTENSION IF NOT EXISTS vector")
    elif not isinstance(database, SqliteDatabase):
        raise RuntimeError(f"Unsupported database for 004_embeddings: {type(database).__name__}")
    database.create_tables([models.UrlEmbedding], safe=True)
    if isinstance(database, PostgresqlDatabase):
        database.execute_sql(
            "CREATE INDEX IF NOT EXISTS idx_urlembedding_embedding_hnsw "
            'ON "urlembedding" USING hnsw ("embedding" vector_cosine_ops)'
        )
