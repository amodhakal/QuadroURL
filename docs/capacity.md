# Postgres connection budget (issue #157)

Postgres `max_connections` is a hard server-side cap on concurrent backend
connections. Every app gunicorn worker process and every consumer process
holds its own `PooledPostgresqlDatabase` pool, so worst-case demand is the
sum of all pools. Size one side without the other and new connections get
refused once the cap is hit.

## Sources of truth

| Setting | File | Default |
|---|---|---|
| Postgres `max_connections` | `docker-compose.yml` (`postgres.command`) | `400` (~397 usable after the superuser reserve) |
| App pool per gunicorn worker process | `app/database.py` (`DB_MAX_CONNECTIONS`) | `20` |
| Gunicorn workers / threads | `Dockerfile` (`GUNICORN_WORKERS` / `GUNICORN_THREADS`, `gthread` class) | `4` workers / `8` threads |
| App `deploy.replicas` | `docker-compose.yml` (`app`) | `3` |
| Consumer logs pool per process | `consumer/config.py` (`DB_MAX_CONNECTIONS_LOGS`) | `10` |
| Consumer events pool per process | `consumer/config.py` (`DB_MAX_CONNECTIONS_EVENTS`) | `10` |
| Consumer creates pool per process | `consumer/config.py` (`DB_MAX_CONNECTIONS_CREATES`) | `5` |
| Consumer `deploy.replicas` (each of logs / events / creates) | `docker-compose.yml` | `3` |

Pool-is-per-process evidence: the app pool is instantiated inside
`init_db()` (`app/database.py`), which runs once per gunicorn worker
process; each consumer builds one module-scope `PooledPostgresqlDatabase`
in `consumer/app.py`. Threads of a `gthread` worker share that worker's
process pool — threads do **not** multiply it.

## Budget table (defaults)

| Service | Per-process pool | Processes | Subtotal |
|---|---|---|---|
| App (gunicorn) | `20` | `3` replicas x `4` workers = `12` | `240` |
| Request-log consumer | `10` | `3` replicas x `1` process = `3` | `30` |
| URL-event consumer | `10` | `3` replicas x `1` process = `3` | `30` |
| URL-create consumer | `5` | `3` replicas x `1` process = `3` | `15` |
| **Total** | | | **`315` of ~397** |

## Scaling rule

Keep worst-case demand at or under **350**, leaving ~50 connections of
headroom under the 400 cap (superuser reserve, monitoring probes, one-off
`psql` sessions):

```text
app_replicas * workers * DB_MAX_CONNECTIONS
  + consumer_replicas * (LOGS + EVENTS + CREATES) <= 350
```

With defaults: `3*4*20 + 3*(10+10+5) = 240 + 75 = 315` (ok).
Scaling hazard: a 4th app replica gives `4*4*20 + 75 = 395` — essentially
at the limit, with no room for anything else.

## What breaks when the budget is exceeded

- New connections fail: Postgres logs `remaining connection slots are
  reserved for non-replication superuser connections` / `sorry, too many
  clients already`, and clients see connection-refused / pool-timeout
  errors (`connect_timeout=5` in both `app/database.py` and
  `consumer/app.py`, so failures surface after ~5 s, not instantly).
- App side: request handlers that need the DB raise through
  `db.connect()`; readiness probes depending on Postgres start failing.
- Consumer side: batch drains (`drain_request_logs`, `drain_url_events`,
  `handle_url_create_batch`) log `Failed to insert batch`-style errors,
  buffers stall, and Kafka offsets stop advancing (consumers back off and
  retry rather than skip — see the `stalled` loops in `consumer/app.py`).

## How to scale safely

- **Raise `max_connections`** (compose `postgres.command`) — simplest, but
  each backend costs shared memory/working set; large values need
  `shared_buffers` / `max_locks_per_transaction` review and a Postgres
  restart.
- **Lower pool sizes** (`DB_MAX_CONNECTIONS`, `DB_MAX_CONNECTIONS_*`) —
  cheap, but under-provisioning the app pool under high thread
  concurrency (8 threads/worker by default) causes pool-exhaustion waits
  instead of Postgres refusals. Same failure shape, different layer.
- **Fewer workers / replicas** — reduces both connection demand and
  serving capacity; prefer this if CPU, not connections, is the binding
  constraint.

Whichever lever you pull, re-run the scaling-rule inequality above and
keep the total under 350.

## Caution: `deploy.replicas` vs `docker compose up`

The `replicas:` values in `docker-compose.yml` live under the `deploy:`
key, which is honored by `docker stack deploy` (Swarm mode). Plain
`docker compose up` is documented to ignore the `deploy` section — use
`docker compose up --scale <service>=N` to actually run N containers
locally. So when auditing the budget against a local `compose up`
deployment, count the containers you really started (`docker compose ps`
/ `--scale` flags), not just the `replicas:` numbers in the file.
