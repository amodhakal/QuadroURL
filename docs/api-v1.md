# API v1

The existing `/users`, `/urls`, `/events`, and `/auth/api-keys` handlers are also
mounted at `/api/v1`. `/logs` and `/fail` have v1 aliases. Existing redirect URLs
remain valid. Health, readiness, metrics and the operations dashboard remain at
their existing operational paths.

- Interactive Swagger UI: `/docs` (also `/api/v1/docs`). The pinned Swagger assets
  load from jsDelivr; the raw specification does not need that CDN.
- OpenAPI 3.1: `/openapi.json` (also `/api/v1/openapi.json`). Request schemas are
  generated from the same Pydantic models used by handlers.
- Registration (`POST /users`) remains public and returns `api_key` once.
  All management requests use `Authorization: Bearer <key>`, including async
  creation status polling. Keys are hashed in storage and not placed in caches.
- `ADMIN_USER_IDS=1,2` grants operator-managed administrator membership. Never
  accept role input during registration. Administrators can manage other owners;
  normal callers can only list/read/change/delete their own users, URLs and events.
  Foreign objects return 404, including before shared object-cache reads.
- Bulk user import, logs, and the chaos kill switch require administrator access.
  The kill switch also retains its independent enable flag and token gate.
- Event creation requires the event user to own its URL. API-key minting always
  uses the authenticated caller, even for administrators.

## Pagination

All v1 lists return:

```json
{"items": [], "pagination": {"offset": 0, "size": 20, "has_more": false,
"next_offset": null, "next_before_id": null}}
```

Use `offset`/`size` (default 0/20, size maximum 200, offset maximum 100000), or
`page`/`per_page`. Database lists are ordered by ascending ID. For descending
keyset navigation supply `before_id` and `size`, then use `next_before_id`.
Do not combine page and offset/cursor styles. Invalid, unknown or repeated query
parameters return 400. No expensive total-count query is performed. Log pages
are newest-first bounded in-memory snapshots, not durable cursor-stable history.

Legacy list shapes stay unchanged for React compatibility: users/URLs return
`{kind: "list", sample: [...]}`, events return an array, logs return `{logs: [...]}`.
List caches include caller, admin state, version, and complete validated filters.

## Validation, status, rate limits

Bodies reject unknown fields, boolean IDs, coercion of strings to booleans,
column-length overflow, unsafe URL schemes, timezone-naive and past expiry.
Errors remain JSON `{error: "field: message"}` with status 400; exact old validation
message wording is not guaranteed. Omitted update fields are preserved; only
`expires_at` can explicitly be cleared with null.

Creation IDs/idempotency keys are server-namespaced by owner before database,
Kafka and Redis use. Poll the returned ID unchanged, with bearer authentication.
Old non-namespaced status IDs require an owned persisted URL (or administrator);
old pending IDs without an owner record are intentionally no longer public.

Rate limits run after authentication on management endpoints, sharing budgets
between legacy and v1 aliases and across keys owned by one user. Public registration
and redirects use trusted client-IP identification. Redis token buckets fail open
on Redis outages. `RATELIMIT_ENABLED=false` disables them; TESTING bypasses them
unless `RATELIMIT_IN_TESTS` is set. Responses include X-RateLimit headers (also on
successful tuple responses), and 429 includes Retry-After.

## Verification and integration notes

`uv run pytest tests/contracts -q` runs SQLite-backed contract tests without
Postgres/Redis/Kafka. The existing integration fixtures need PostgreSQL and were
not available for this work. Existing tests relying on arbitrary cross-owner
access must authenticate as the resource owner or explicitly configure an admin.

Other feature branches adding export, analytics, custom aliases, etc. must use
`require_auth`, `assert_owner`, and owner-scoped queries before reading caches or
serializing data. Those routes are not present in this base branch and are not
covered by this change. OpenAPI includes live v1 routes; new feature-specific
request/response contracts should be added alongside those features.
