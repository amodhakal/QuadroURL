# CSV and JSON exports (#185)

Authenticated GET `/api/v1/exports/users`, `/exports/urls`, `/exports/events`
(the latter two paths also belong under `/api/v1`; all legacy `/exports/...`
routes remain available). Supply `Authorization: Bearer <key>`.

Parameters: `format=json|csv` (default json), `limit=1..1000` (default 1000),
`after_id` (default 0). Results are ordered by increasing ID. JSON contains
`data` and `pagination: {limit, has_more, next_after_id}`. CSV includes a header
row; `X-Has-More` and `X-Next-After-Id` provide continuation. Request each next
page until has_more is false. Pages are not a consistent database snapshot;
concurrent edits/deletions can change later pages.

Ordinary users see only their own user row, URLs, and events. Operators listed
in ADMIN_USER_IDS can export all owners. Credentials and internal idempotency
keys are never exported. Responses prohibit caching and use attachment filenames.
CSV uses standard quoting plus an apostrophe prefix for formula-leading cells;
JSON preserves original text. This deliberate safety transformation means CSV
is not an exact round-trip for formula-like text. Bearer rate limit: burst 10,
refill 1/second using the existing Redis limiter's fail-open policy.

Targeted checks cover ownership, cursor continuation, bounds, credentials, and
CSV quoting/formula neutralization. No full test suite was run.
