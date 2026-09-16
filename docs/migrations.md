# Database migrations

Run `uv run python -m migrations_runner upgrade` **before** starting API workers
or consumers. It reads DATABASE_HOST/PORT/NAME/USER/PASSWORD. `status` lists
applied/pending versions. Startup no longer issues DDL. Compose runs a one-shot
`migrate` service and gates API startup on successful completion. For standalone
consumers, deployments must gate worker startup on this same command.

Numbered forward-only modules in `migrations/` expose `upgrade(database, models)`.
A PostgreSQL advisory transaction lock serializes upgrades; DDL and history commit
together. SQLite uses an immediate transaction for focused tests. Unknown history
versions fail closed. Back up the database before upgrading; there is no automatic
downgrade—restore the backup or apply a reviewed forward repair.

The baseline adopts existing tables without deleting rows, adding legacy missing
`url.expires_at` and `url.request_id` columns and the canonical indexes. It does not
repair arbitrary hand-edited schemas. Do not edit applied migration modules; add
a new numbered migration for future changes (including model field changes).

Verified for this PR: SQLite runner tests; real PostgreSQL 16 CLI fresh install,
repeat upgrade (no-op), and legacy URL adoption with original rows retained.
No full application test suite was run.
