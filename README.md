# QuadroURL

**Stack:** Flask · Peewee ORM · PostgreSQL · Redis · Kafka · Docker · Locust

A URL shortener API with comprehensive caching, metrics, request audit logging via Kafka, and load testing infrastructure.

---

## Quick Start

### Prerequisites

- **Docker** and **Docker Compose** installed
- **Git** installed

### 1. Clone and configure

```bash
git clone <repo-url>
cd QuadroURL
cp .env.example .env
```

Edit `.env` if needed — defaults work out of the box for local development.

### 2. Start all services

```bash
docker compose up --build
```

### 3. Verify

```bash
curl http://127.0.0.1/health
# → {"status": "ok"}
```

---

## How to Run

### With Docker Compose (recommended)

```bash
docker compose up --build
```

This starts 10 services: app (3 replicas), PostgreSQL, Redis, Kafka, Zookeeper, request-log-consumer, Nginx, Prometheus, Grafana, and Loki+Promtail.

### Without Docker (local development)

Requires a running PostgreSQL and Redis instance.

```bash
# Install dependencies
uv sync

# Start the app (Flask dev server on port 5000)
uv run run.py

# Or start with gunicorn (port 8000, matching Docker)
uv run gunicorn --bind 0.0.0.0:8000 --workers 4 --threads 4 --worker-class gthread --timeout 5 run:app
```

Set `FLASK_DEBUG=true` in `.env` for debug mode.

### Accessing Services

| Service | URL | Credentials |
|---------|-----|-------------|
| App (via Nginx) | http://127.0.0.1 | - |
| App (direct, dev) | http://localhost:5000 | - |
| Prometheus | http://localhost:9090 | - |
| Grafana | http://localhost:3000 | admin / admin |
| Loki | http://localhost:3100 | - |

### Stopping Services

```bash
docker compose down
```

To also remove volumes (database data):

```bash
docker compose down -v
```

---

## API Reference

### Health Check

```
GET /health
```

**Response:** `200 OK`
```json
{"status": "ok"}
```

---

### Users

#### Create User

```
POST /users
Content-Type: application/json
```

**Request body:**
```json
{
  "username": "testuser",
  "email": "testuser@example.com"
}
```

**Response:** `201 Created`
```json
{
  "id": 1,
  "username": "testuser",
  "email": "testuser@example.com",
  "created_at": "2026-04-03T12:00:00"
}
```

**Errors:** `400` — invalid JSON, missing fields, or duplicate username/email.

---

#### List Users

```
GET /users?page=1&per_page=20
```

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `page` | int | `1` | Page number (1-indexed) |
| `per_page` | int | `20` | Items per page |

**Response:** `200 OK`
```json
{
  "kind": "list",
  "sample": [
    {
      "id": 1,
      "username": "testuser",
      "email": "testuser@example.com",
      "created_at": "2026-04-03T12:00:00"
    }
  ]
}
```

---

#### Get User by ID

```
GET /users/<id>
```

**Response:** `200 OK` — single user object.

**Errors:** `404` — user not found.

---

#### Update User

```
PUT /users/<id>
Content-Type: application/json
```

**Request body:**
```json
{
  "username": "updated_name"
}
```

**Response:** `200 OK` — updated user object.

**Errors:** `400` — invalid JSON. `404` — user not found.

---

#### Delete User

```
DELETE /users/<id>
```

**Response:** `200 OK`
```json
{}
```

---

#### Bulk Import Users (CSV)

```
POST /users/bulk
Content-Type: multipart/form-data
```

| Field | Type | Description |
|-------|------|-------------|
| `file` | file | CSV file with `username` and `email` columns |

**Response:** `200 OK`
```json
{"imported": 25}
```

**Note:** Drops and recreates the entire `User` table on each import.

---

### URLs

#### Create URL

```
POST /urls
Content-Type: application/json
```

**Request body:**
```json
{
  "user_id": 1,
  "original_url": "https://example.com/long-page",
  "title": "Example Page"
}
```

**Response:** `202 Accepted`
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending"
}
```

URL creation is processed asynchronously via Kafka. Poll the status endpoint to get the short code.

**Errors:** `400` — invalid JSON, missing fields, or user not found.

---

#### Get URL Status (Poll after Create)

```
GET /urls/<request_id>/status
```

**Response:** `200 OK`
```json
{
  "status": "ready",
  "id": 1,
  "short_code": "k8Jd9s",
  "original_url": "https://example.com/long-page",
  "title": "Example Page"
}
```

While pending:
```json
{"status": "pending"}
```

On error:
```json
{"status": "error", "error": "Failed to generate unique short code"}
```

**Errors:** `404` — request_id not found (expired after 1 hour).

---

#### List URLs

```
GET /urls?offset=0&size=20&user_id=1&is_active=true
```

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `offset` | int | `0` | Pagination offset |
| `size` | int | `20` | Page size |
| `id` | int | — | Filter by URL id |
| `user_id` | int | — | Filter by user id |
| `short_code` | string | — | Filter by short code |
| `original_url` | string | — | Filter by original URL |
| `is_active` | string | — | `"true"` = active, else inactive |

**Response:** `200 OK`
```json
{
  "kind": "list",
  "sample": [
    {
      "id": 1,
      "user_id": 1,
      "short_code": "k8Jd9s",
      "original_url": "https://example.com/long-page",
      "title": "Example Page",
      "is_active": true,
      "created_at": "2026-04-03T12:00:00",
      "updated_at": "2026-04-03T12:00:00"
    }
  ]
}
```

---

#### Get URL by ID

```
GET /urls/<id>
```

**Response:** `200 OK` — single URL object.

**Errors:** `404` — URL not found.

---

#### Update URL

```
PUT /urls/<id>
Content-Type: application/json
```

**Request body:**
```json
{
  "title": "Updated Title",
  "is_active": false
}
```

**Response:** `200 OK` — updated URL object.

**Errors:** `400` — invalid JSON. `404` — URL not found.

---

#### Delete URL

```
DELETE /urls/<id>
```

**Response:** `200 OK`
```json
{}
```

---

#### Redirect by Short Code

```
GET /urls/<short_code>/redirect
```

**Response:** `302 Found` — redirects to `original_url`.

**Errors:** `404` — short code not found or URL inactive.

---

#### Get URL by Short Code (JSON)

```
GET /r/<short_code>
```

**Response:** `200 OK`
```json
{
  "url": "https://example.com/long-page",
  "short_code": "k8Jd9s"
}
```

**Errors:** `404` — short code not found or URL inactive.

---

### Events

#### List Events

```
GET /events?offset=0&size=20&user_id=1&event_type=click
```

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `offset` | int | `0` | Pagination offset |
| `size` | int | `20` | Page size |
| `url_id` | int | — | Filter by URL id |
| `user_id` | int | — | Filter by user id |
| `event_type` | string | — | Filter by type (`created`, `updated`, `click`) |

**Response:** `200 OK`
```json
[
  {
    "id": 1,
    "url_id": 1,
    "user_id": {"id": 1},
    "event_type": "created",
    "timestamp": "2026-04-03T12:00:00",
    "details": {"short_code": "k8Jd9s", "original_url": "https://..."}
  }
]
```

---

#### Create Event

```
POST /events
Content-Type: application/json
```

**Request body:**
```json
{
  "url_id": 1,
  "user_id": 1,
  "event_type": "click",
  "details": {"source": "dashboard"}
}
```

**Response:** `201 Created` — event object.

**Errors:** `400` — invalid JSON, missing fields, URL not found, or user not found.

---

### Metrics

```
GET /metrics
```

**Response:** `200 OK`
```json
{
  "system": {
    "cpu_percent": 8.2,
    "system_ram": {"used_gb": 6.4, "total_gb": 15.9},
    "process_memory_mb": 91.7
  },
  "latency": {"avg_ms": 15.2, "p50_ms": 12.0, "p95_ms": 22.0, "p99_ms": 210.0, "max_ms": 450.0},
  "traffic": {"total_requests": 847, "requests_per_second": 4.2, "top_endpoints": [{"endpoint": "POST /urls", "count": 120}]},
  "errors": {"total_errors": 102, "error_rate_percent": 12.0, "by_status": {"400": 102}},
  "saturation": {"active_requests": 1, "peak_active_requests": 3},
  "uptime_seconds": 3600.0,
  "recent_requests": [{"method": "POST", "path": "/urls", "status": 400, "latency_ms": 2.1}]
}
```

---

### Logs

```
GET /logs
```

**Response:** `200 OK` — last 50 structured log records.
```json
{
  "logs": [
    {
      "timestamp": "2026-04-03T12:00:00+00:00",
      "level": "INFO",
      "logger": "app",
      "message": "Health check requested",
      "method": "GET",
      "path": "/health",
      "remote_addr": "127.0.0.1"
    }
  ]
}
```

---

### Dashboard

```
GET /dashboard
```

**Response:** `200 OK` — self-contained HTML page with live-updating charts (latency, errors, traffic, system resources) that polls `/metrics` and `/logs` every 3 seconds.

---

### Prometheus Metrics

```
GET /prometheus-metrics
```

**Response:** `200 OK` — Prometheus text exposition format. Metrics include:

| Metric | Type | Labels |
|--------|------|--------|
| `http_requests_total` | Counter | `method`, `endpoint`, `status` |
| `http_request_duration_seconds` | Histogram | `method`, `endpoint` |
| `http_requests_in_progress` | Gauge | — |
| `http_errors_total` | Counter | `method`, `endpoint`, `status` |
| `process_cpu_percent` | Gauge | — |
| `process_memory_mb` | Gauge | — |

---

### Chaos Endpoint

```
GET /fail
```

Kills the process immediately. Used for chaos/crash testing. No response (process exits).

---

### Error Responses

All errors return JSON:

| Status | Body |
|--------|------|
| `400` | `{"error": "<description>"}` |
| `404` | `{"error": "Not found"}` |
| `422` | `{"error": "Unprocessable entity"}` |
| `500` | `{"error": "<exception>"}` |

---

## Load Testing

### Local Sweep — `scripts/run_load_tests.sh`

Automates a clean, repeatable Locust benchmark sweep against the local Docker Compose stack.

```bash
# Default: 2000, 3000, 4000, 5000 users
./scripts/run_load_tests.sh

# Custom levels, spawn rate, peak time
./scripts/run_load_tests.sh --levels 500,1000,2000 --spawn-rate 50 --peak-time 120

# Multi-process mode (avoids generator bottleneck at high user counts)
./scripts/run_load_tests.sh --levels 3000,4000,5000 --processes -1

# Target a different host
./scripts/run_load_tests.sh --host http://127.0.0.1 --locust-file scripts/test_locust.py
```

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--levels` | `2000,3000,4000,5000` | Comma-separated user counts to test |
| `--spawn-rate` | `100` | Users spawned per second |
| `--peak-time` | `60` | Seconds of steady-state load after ramp-up |
| `--run-time` | auto | Fixed run-time per level (overrides auto-computed) |
| `--processes` | single | `-1` for auto (one worker per CPU core) |
| `--host` | `http://127.0.0.1` | Target host |
| `--locust-file` | `scripts/test_locust.py` | Locust test file |

**How auto run-time works:** Each level gets `ceil(users / spawn-rate) + peak-time` seconds, so higher user counts automatically get a longer test window instead of spending most of a fixed duration still ramping up.

**What it does per level:**
1. Full Docker Compose reset (`down -v` + `up --build -d`)
2. Waits for `/health` to pass
3. Runs Locust headless at the target concurrency
4. Saves CSV results to `loadtests/results_<timestamp>/`

Results are saved per-level with `_stats.csv`, `_failures.csv`, and `_stats_history.csv` suffixes.

---

### AWS Load Testing

A complete Terraform setup for load testing at ~13,000 req/s with zero cross-network data transfer costs.

**Architecture:**
```
┌───────────────────────── VPC (10.0.0.0/16) ─────────────────────────┐
│                                                                      │
│  Public Subnets:                                                     │
│  ┌────────────┐     ┌────────────────────────────────┐               │
│  │    ALB     │────▶│  ECS Fargate Spot (4 tasks)    │               │
│  └────────────┘     └───────────────┬────────────────┘               │
│                                     │                                │
│  Private Subnets:                   │                                │
│  ┌───────────────────┐     ┌────────▼─────────────┐                  │
│  │ PostgreSQL (RDS)  │     │ ElastiCache (Redis)  │                  │
│  └───────────────────┘     └──────────────────────┘                  │
│                                                                      │
│  ┌───────────────────────────────────────────┐                        │
│  │ EC2 (Locust load tester) - c6i.xlarge Spot│                        │
│  └───────────────────────────────────────────┘                        │
└──────────────────────────────────────────────────────────────────────┘
```

**Estimated cost:** ~$0.53/hour (~$1.06 for a 2-hour test run).

#### Provision & Deploy

```bash
# Initialize and create infrastructure
cd terraform
terraform init
terraform apply -auto-approve

# Push Docker image to ECR
./scripts/deploy.sh
```

#### Run Load Test

```bash
# SSH to loadtester and run 5000-user headless test for 10 minutes
./scripts/run_loadtest.sh
```

This runs on the EC2 load tester inside the same VPC (zero network cost), targeting the ALB.

#### Tear Down

```bash
cd terraform
terraform destroy -auto-approve
```

**Always destroy immediately after testing to avoid ongoing charges.**

---

## Architecture

### Local (Docker Compose)

```
                         ┌─────────────┐
                         │    User     │
                         └──────┬──────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         app-network                                      │
│  ┌─────────┐      ┌───────────┐      ┌─────────────────┐               │
│  │  nginx  │─────▶│   app     │─────▶│   postgres      │               │
│  │ :80     │      │ (3 pods)  │      │   :5432         │               │
│  └─────────┘      └─────┬─────┘      └────────▲────────┘               │
│                         │                      │                        │
│                         │ publish              │ batch insert           │
│                         ▼                      │                        │
│                    ┌───────────┐  poll  ┌──────┴──────────────┐         │
│                    │   kafka   │◀───────│ request-log-consumer │         │
│                    │  :9092    │        └─────────────────────┘         │
│                    └─────┬─────┘                                        │
│                          │                                              │
│                    ┌─────┴─────┐      ┌───────────┐                     │
│                    │ zookeeper │      │   redis   │                     │
│                    │  :2181    │      │  :6379    │                     │
│                    └───────────┘      └───────────┘                     │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐                           │
│  │prometheus │  │  grafana  │  │   loki    │                           │
│  │  :9090    │  │  :3000    │  │  :3100    │                           │
│  └───────────┘  └───────────┘  └───────────┘                           │
└─────────────────────────────────────────────────────────────────────────┘
```

### Kafka Pipeline

The app uses three Kafka topics for asynchronous processing:

```
Flask app ──┬── after_request ──▶ request-logs topic ──▶ Consumer ──▶ RequestLog table
            ├── create/update/click ──▶ url-events topic ──▶ Consumer ──▶ Event table
            └── POST /urls ──▶ url-creates topic ──▶ Consumer ──▶ Url table + Redis
```

#### Topic: `request-logs` (drain every 5s)

Logs every HTTP request with full metadata:

| Field | Example |
|-------|---------|
| `user_agent` | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...` |
| `client_ip` | `172.18.0.1` |
| `method` | `GET` |
| `path` | `/r/k8Jd9s` |
| `status_code` | `302` |
| `latency_ms` | `12.34` |
| `short_code` | `k8Jd9s` |

#### Topic: `url-events` (drain every 1s)

Business events (clicks, creates, updates) that were previously written by a 2-thread ThreadPoolExecutor. Now batch-consumed by Kafka for better throughput and no FK race conditions.

#### Topic: `url-creates` (processed individually)

Async URL creation. Consumer generates short codes, inserts into PostgreSQL, and sets a Redis key (`url-pending:{request_id}`) for the client to poll.

#### Consumer Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `KAFKA_BROKER` | `kafka:9092` | Kafka bootstrap server |
| `KAFKA_GROUP` | `request-log-writer` | Consumer group ID |
| `KAFKA_TOPIC_REQUEST_LOGS` | `request-logs` | Access log topic |
| `KAFKA_TOPIC_URL_EVENTS` | `url-events` | Business event topic |
| `KAFKA_TOPIC_URL_CREATES` | `url-creates` | URL creation topic |
| `DRAIN_INTERVAL_LOGS` | `5` | Seconds between log batch inserts |
| `DRAIN_INTERVAL_EVENTS` | `1` | Seconds between event batch inserts |
| `BATCH_SIZE_LOGS` | `1000` | Max log messages before flush |
| `BATCH_SIZE_EVENTS` | `500` | Max event messages before flush |
| `REDIS_URL` | `redis://redis:6379/0` | Redis for pending URL status |

### Design Decisions

- **Redis** — L2 cache between app replicas to prevent redundant DB calls. L1 (in-process LRU dict) avoids Redis latency for hot keys. Anti-stampede strategies: single-flight deduplication, probabilistic early expiration, negative caching, TTL jitter. Also stores pending URL creation status for async polling.
- **Kafka** — Decouples all write operations from the request path. Three topics with different drain intervals: 5s for access logs, 1s for business events, immediate processing for URL creation. Replaces the broken 2-thread ThreadPoolExecutor that had FK race conditions and a memory leak.
- **Nginx** — Reverse proxy and load balancer across app replicas. Connection timeouts (5s connect, 10s read) prevent slow clients from holding connections.
- **Docker Compose** — Containerized orchestration with health checks, restart policies, and persistent volumes for PostgreSQL, Redis, and Grafana.
- **Locust** — Python-native load testing (same stack as the app). Weighted task distribution simulates realistic user behavior.

---

## Monitoring & Observability

### Grafana Dashboard

Access at http://localhost:3000 (admin/admin). The pre-built "Golden Signals" dashboard shows:

- **Latency** — avg, p50, p95, p99 response times
- **Traffic** — requests per second, top endpoints by volume
- **Errors** — error rate, error breakdown by status code
- **Saturation** — active requests, peak concurrent requests
- **System** — CPU, memory, process RSS

### Prometheus Metrics

Scrapes `app:8000/prometheus-metrics` every 5 seconds. Access at http://localhost:9090.

### Live Logs (Loki)

Structured JSON logs are collected by Promtail and stored in Loki. Query via Grafana's Explore view or the `/logs` API endpoint.

### Discord Alerts

A background monitor polls `/health` every 60 seconds. Sends Discord webhook alerts on:
- **Service Down** — health check fails or returns non-200
- **Service Recovered** — health check returns to healthy

Configure via `DISCORD_WEBHOOK_URL` and `APP_URL` in `.env`.

---

## Operations

### Health Checks

```bash
# Quick check
curl http://127.0.0.1/health

# Detailed metrics
curl http://127.0.0.1/metrics

# Recent logs
curl http://127.0.0.1/logs
```

### Alert Playbooks

#### Service Down

1. Confirm with `GET /health`
2. Check `GET /logs` for recent exceptions
3. Check `GET /metrics` for CPU/memory spikes
4. Verify PostgreSQL is running and credentials match
5. Restart app container if hung
6. Restart PostgreSQL if connections are failing
7. If a bad deploy caused it, roll back
8. Confirm `GET /health` returns `{"status": "ok"}`

#### Service Recovered

1. Record incident start/end times
2. Capture root cause from logs, deploy history, or DB state
3. Update incident ticket with the fix
4. Add prevention actions if user-facing

#### Health Check Degraded (app still up)

1. Call `GET /health` and capture body
2. Check `GET /logs` for exceptions
3. Check PostgreSQL reachability
4. Review recent deploys or config changes
5. Restore last known good config, re-test `/health`

### Deployment

**CI:** GitHub Actions runs `pytest` on every push/PR. Deployment is blocked if tests fail.

**Pre-deploy:**
```bash
uv run pytest
```

**Manual rollback:**
1. Identify last good commit SHA
2. `git revert` or re-deploy that commit
3. Verify `GET /health` returns healthy

### Escalation

- **Primary:** currently assigned on-call engineer
- **Secondary:** any teammate with deploy access
- If outage exceeds 30 minutes, open a GitHub issue titled `[INCIDENT] short summary` with dashboard screenshots and logs

---

## Troubleshooting

### Common Failures

#### App won't start (ImportError / missing package)

```bash
uv sync
uv run run.py
```

#### DB connection errors

- Confirm PostgreSQL is running
- Check `.env` values: `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_NAME`
- Create the database: `createdb hackathon_db`

#### Health endpoint failing

- Verify app process is running
- Check `GET /logs` for stack traces
- Check DB connectivity
- If only non-critical downstreams fail, the app degrades gracefully

#### Invalid client payloads

- `400` responses with `{"error": "..."}` indicate bad input
- Check `user_id` is an integer (not a string)
- Check required fields are present (`username`/`email` for users, `user_id`/`original_url`/`title` for URLs)

### Incident Response Checklist

1. Confirm incident with `GET /health`
2. Collect evidence from `GET /logs` and `GET /metrics`
3. Identify whether failure is app-level, DB-level, or request-schema-level
4. Apply targeted recovery (restart app, recover DB, rollback bad deploy, or fix request)
5. Verify recovery by re-running failing request and `/health`
6. Record timeline and root cause

### Debugging Example

**Reported issue:** Users report intermittent URL creation failures.

1. **Dashboard** (`GET /dashboard`): Error rate jumped from 0% to ~12%
2. **Error Breakdown**: 85% are `400 Bad Request`, 0% are `500` — server is healthy
3. **Top Endpoints**: `POST /urls` is the busiest
4. **Logs** (`GET /logs`): Repeated `user_id must be an integer` warnings
5. **Root cause**: Client sending `"user_id": "1"` (string) instead of `"user_id": 1` (integer)
6. **Fix**: Client corrects payload format. No server changes needed.

Time to diagnosis: under 2 minutes using only the dashboard and logs.

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/test_locust.py` | Locust load test — weighted tasks simulating real user behavior |
| `scripts/run_load_tests.sh` | Automated sweep — Docker reset between levels, CSV results per level |
| `scripts/generate_traffic.py` | Traffic generator for populating Grafana dashboards |
| `scripts/load_csv.py` | Bulk load CSV seed data into PostgreSQL |
| `terraform/scripts/deploy.sh` | AWS deploy — Terraform apply + ECR push + ECS force-deploy |
| `terraform/scripts/run_loadtest.sh` | AWS load test — SSH to EC2, run headless Locust, download results |

---

## Project Structure

```text
QuadroURL/
├── app/
│   ├── __init__.py          # App factory (create_app), logging, middleware, health endpoint
│   ├── database.py          # DatabaseProxy, BaseModel, PooledPostgresqlDatabase
│   ├── cache.py             # L1/L2 cache with anti-stampede (single-flight, early expiry, negative cache)
│   ├── log_store.py         # In-memory log buffer
│   ├── metrics_store.py     # In-memory golden signals (latency, traffic, errors, saturation)
│   ├── models/
│   │   ├── user.py          # User(id, username, email, created_at)
│   │   ├── url.py           # Url(id, user FK, short_code, original_url, title, is_active, timestamps)
│   │   ├── event.py         # Event(id, url FK, user FK, event_type, timestamp, details)
│   │   └── request_log.py   # RequestLog(id, url FK, user_agent, client_ip, method, path, status_code, latency_ms, short_code)
│   ├── routes/
│   │   ├── users.py         # CRUD + bulk CSV import
│   │   ├── urls.py          # Async create (202), status poll, CRUD + redirect
│   │   ├── events.py        # List + create events
│   │   ├── metrics.py       # GET /metrics (golden signals)
│   │   ├── logs.py          # GET /logs (in-memory buffer)
│   │   ├── prometheus.py    # GET /prometheus-metrics
│   │   ├── dashboard.py     # GET /dashboard (live HTML dashboard)
│   │   └── fail.py          # GET /fail (chaos endpoint)
│   └── utils/
│       ├── alerts.py        # Discord webhook health-check monitor
│       ├── events.py        # Event publishing via Kafka (replaces ThreadPoolExecutor)
│       ├── kafka_producer.py # Kafka producer for all topics (logs, events, url-creates)
│       ├── logger.py        # JSONFormatter helper
│       └── ratelimit.py     # Distributed token-bucket rate limiting (Redis Lua)
├── consumer/                # Kafka consumer service (separate container)
│   ├── Dockerfile           # Python 3.13-slim consumer image
│   ├── requirements.txt     # confluent-kafka, psycopg2, peewee, redis
│   ├── config.py            # Per-topic configuration (drain intervals, batch sizes)
│   ├── app.py               # Multi-topic consumer: 3 threads for 3 topics
│   └── url_create_handler.py # URL creation logic (short code gen, DB insert, Redis status)
├── tests/                   # 12 test files, ~1100+ lines
├── scripts/
│   ├── test_locust.py       # Locust load test
│   ├── run_load_tests.sh    # Automated sweep runner
│   ├── generate_traffic.py  # Grafana traffic generator
│   └── load_csv.py          # CSV seed data loader
├── terraform/               # AWS load testing infrastructure
│   ├── main.tf              # AWS provider, tags
│   ├── variables.tf         # Region, project name, DB password, task count
│   ├── vpc.tf               # VPC, public/private subnets, IGW
│   ├── security_groups.tf   # ALB, ECS, DB, Redis, loadtester SGs
│   ├── ecr.tf               # ECR repository
│   ├── rds.tf               # PostgreSQL db.t4g.medium
│   ├── elasticache.tf       # Redis cache.t4g.medium
│   ├── alb.tf               # ALB + target group + listener
│   ├── ecs.tf               # ECS cluster, Fargate Spot, IAM, CloudWatch
│   ├── loadtester.tf        # c6i.xlarge Spot instance
│   ├── outputs.tf           # ALB DNS, loadtester IP, ECR URL
│   └── scripts/
│       ├── deploy.sh        # Provision + ECR push + ECS deploy
│       └── run_loadtest.sh  # SSH + headless Locust
├── loadtests/               # Load test results (CSVs, reports)
├── data/                    # CSV seed data (users, urls, events)
├── nginx/nginx.conf         # Reverse proxy to app:8000
├── prometheus/prometheus.yml # Scrapes app:8000/prometheus-metrics every 5s
├── grafana/                 # Dashboards + provisioning
├── promtail/                # Docker log collection to Loki
├── docker-compose.yml       # 10 services: app, postgres, redis, kafka, zookeeper, request-log-consumer, nginx, prometheus, grafana, loki+promtail
├── Dockerfile               # Python 3.13-slim, uv, gunicorn (env-configurable workers)
├── run.py                   # Entry point: create_app() + app.run()
├── pyproject.toml           # Package metadata (uv)
├── .env.example             # Environment variable template
└── README.md
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_NAME` | `hackathon_db` | PostgreSQL database name |
| `DATABASE_HOST` | `localhost` | PostgreSQL host |
| `DATABASE_PORT` | `5432` | PostgreSQL port |
| `DATABASE_USER` | `postgres` | PostgreSQL user |
| `DATABASE_PASSWORD` | `postgres` | PostgreSQL password |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL for L2 cache |
| `KAFKA_BROKER` | `kafka:9092` | Kafka bootstrap server |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |
| `APP_URL` | `http://127.0.0.1:5000` | App URL for Discord health monitor |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook for alerts |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` for verbose logging |
| `GUNICORN_WORKERS` | `4` | Number of gunicorn worker processes |
| `GUNICORN_THREADS` | `4` | Threads per gunicorn worker |

---

## Running Tests

```bash
# Run all tests
uv run pytest tests/

# Run with coverage
uv run pytest tests/ --cov=app --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_cache_stampede.py
```
