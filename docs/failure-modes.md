# Failure modes: fail-open vs fail-closed

Principle: **reads and observability fail open; writes and readiness fail closed.**

- A degraded cache, limiter, or log sink must never take down serving traffic.
- Anything that records caller-visible state (Kafka writes, URL-create
  acknowledgement, `/ready`, the async status store) must surface failure
  instead of silently dropping it.

## Per-component decisions

| Component | Failure mode | Behavior | Open / Closed |
|---|---|---|---|
| Cache L2 reads (`get_user` / `get_url` / `get_url_by_short_code` via `_l2_safe`) | Redis error or `get_l2()` returns `None` | Treated as a miss; falls through to the DB via `_resolve_miss` | Open |
| Cache L2 writes (`set_*` / `delete_*` / `clear_*` via `_l2_fire_and_forget`) | Redis down, or executor submit fails | L1 is still updated; the error is swallowed | Open |
| `get_l2()` bootstrap | Redis unreachable | Returns `None` and marks L2 unavailable for a 30 s cooldown (`#117`) | Open (degrades) |
| Rate limiter, Redis unavailable | `get_l2()` returns `None` | Request is allowed | Open |
| Rate limiter, Redis script error | Any exception from the token-bucket script | `logger.error`, request is allowed | Open |
| `/ready` | Postgres, Redis, or Kafka check fails | `503 {"status": "not_ready", "checks": {...}}` | Closed |
| `/health` | Any dependency down | Still `200 {"status": "ok"}` — pure liveness, touches nothing | Open (by design) |
| `track_metrics` (`after_request`) | `publish_log_event` raises | `app.logger.exception`, response unchanged; `REQUESTS_IN_PROGRESS` still decremented in `finally` | Open |
| Click tracking (`track_click`) | `create_event` raises | `app.logger.exception`, redirect unaffected; bots skipped before any write | Open |
| `_produce`, buffer full past `KAFKA_PRODUCE_TIMEOUT` | Kafka backpressure | Logs `queue full … message dropped`, raises `ProducerBackpressureError` | Closed |
| `_produce`, other client errors | Any non-`BufferError` exception | Logs, re-raises — never silently dropped | Closed |
| `GET /urls/<request_id>/status` | Status store (`Redis`) down or errors | `503 "Status store unavailable"` | Closed |
| `GET /fail` chaos switch | `CHAOS_ENABLED != "true"` | `404`; wrong `CHAOS_TOKEN` → `403`; triggered → `os._exit(1)` | Closed by default |

Notes:

- `_resolve_miss` follower path: a waiter that finds an empty cache after the
  primary finishes (or times out after 30 s) does one direct DB fetch, and
  returns `None` only if that fetch also fails (`#116`). See deliberately
  unchanged candidate 1 below.
- Cache L2 read/write failures are silent (no log line, no gauge) — the only
  operator-visible symptom is higher DB load/latency. See candidate 2.
- Prometheus request counters/latency are recorded *before* the Kafka log
  publish attempt in `track_metrics`, so a logging outage does not skew
  request metrics either.

## Environment kill-switches

| Variable | Default | Effect |
|---|---|---|
| `RATELIMIT_ENABLED` | `"true"` | Any other value disables rate limiting entirely (e.g. load tests); requests bypass Redis |
| `TESTING` (Flask config) | — | Rate limiter bypassed so the suite never 429s itself |
| `KAFKA_SYNC_FALLBACK` | `"1"` in tests (`tests/conftest.py`) | `publish_*` write straight to the DB instead of Kafka (tests / local dev); DB errors then propagate to the caller |
| `KAFKA_PRODUCE_TIMEOUT` | `5.0` (seconds) | How long `_produce` retries a full buffer before raising `ProducerBackpressureError` |
| `CHAOS_ENABLED` | `"false"` | Must be `"true"` for `/fail` to do anything; pair with `CHAOS_TOKEN` (`#100`) |
| `ALERT_MONITOR_ENABLED` | `"false"` | Discord crash monitor only starts when `"true"` (same-process monitor cannot detect a real crash) |

## Operator-visible effects

- **Rate limited (429):** `X-RateLimit-Limit` / `X-RateLimit-Remaining` /
  `X-RateLimit-Reset` plus `Retry-After`; body
  `{"error": "Rate limit exceeded", "retry_after": N}`.
- **Rate limiter degraded (allowed despite Redis down):** one
  `quadroPE.ratelimit` `ERROR "Rate limiter error: …"` per failed check;
  traffic unaffected.
- **Readiness:** `WARNING "Readiness <dep> check failed: …"` per failing dep;
  Kafka probe result cached for 10 s so transient blips do not flap LB
  membership (`#140`). Unready body: `{"status": "not_ready", "checks":
  {"postgres": …, "redis": …, "kafka": …}}` with HTTP 503.
- **Observability degraded:** `ERROR "Failed to publish request log to Kafka"`
  (traceback included); response status/body unchanged; `REQUESTS_IN_PROGRESS`
  gauge still decremented.
- **Kafka backpressure:** `ERROR "Kafka producer queue full for
  topic=…"` followed by a `ProducerBackpressureError` surfacing to the
  caller (async URL create → 5xx, not a silent drop). Delivery callback
  failures are counted in `delivery_stats()` and logged with topic context
  (`#139`).
- **Status store down:** `WARNING "Status store error: …"`; callers get
  `503 "Status store unavailable"`. Corrupt payloads → `500 "Corrupted
  status payload"`.
- **Chaos switch:** `WARNING "Chaos kill-switch triggered via /fail"`, then
  the process exits(1).
