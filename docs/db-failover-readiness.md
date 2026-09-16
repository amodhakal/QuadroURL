# Multi-region DB failover readiness (#187)

## What /ready checks now

`GET /ready` upgrades its database check from "connected" (`SELECT 1`) to
"**writable primary**" (`app/db_readiness.py`). A connection that succeeds while
`pg_is_in_recovery()` is true, or while the session reports read-only, fails the
check with HTTP 503, so a load balancer stops sending writes to a promoted
standby before the standby accepts them. `/health` remains a liveness check and
does not touch any dependency. Redis and Kafka checks are unchanged.

`/ready` is excluded from the per-request DB connect hook and opens its own
connection inside the guarded probe, so a down database still returns a JSON
503 instead of an unhandled error.

## Deployment steps for failover topologies

1. **Promote reads/writes to one writer endpoint.** Point `DATABASE_HOST` for
   every app replica and consumer (in Compose, Kubernetes ConfigMap, or platform
   secrets) at the current writer endpoint. A DNS name or managed endpoint that
   can be re-pointed is strongly preferred over hardcoded IPs.
2. **Replicas stay out of rotation automatically.** Any instance whose
   `DATABASE_HOST` resolves to a standby fails `/ready` (503) and should be
   removed by the platform load balancer. This is the readiness signal only:
   nothing in the application re-routes requests between regions.
3. **Trigger failover with your existing mechanism** (managed promotion, or a
   quorum/consensus tool of the deployment platform). The readiness endpoint
   reflects the outcome, it does not decide or trigger promotion.
4. **After promotion:** update/re-point the writer endpoint, wait for replicas
   to pass `/ready` again, and confirm replication catches up before restoring
   full traffic. Monitor `checks.postgres` on each instance.
5. **Verification before relying on it in staging/production:** promote a
   standby deliberately (or `pg_ctl promote` a test replica), observe `/ready`
   flip to 503 on the demoted node and back to 200 after re-pointing, then
   reverse. Nothing in this change was validated against a live standby or a
   real failover event — that rehearsal is still required.

## Validated

Focused unit tests (`tests/test_db_readiness.py`) cover writable-primary pass,
standby / read-only-session / empty-result rejection, connection-error
propagation, statement-order (timeout before probe) and the in-transaction
guard. The live-surface tests in `tests/test_readiness.py` were also run. An
ad-hoc integration check against a local `postgres:16` container confirmed the
guard fires inside an explicit `atomic()` transaction, accepts a normal
connection, and rejects a read-only session — this is not evidence about any
production failover behavior.

## Limitations and non-goals

- **The probe checks the role of the one database it connects to.** It does not
  inspect replication lag, replica sets, WAL state, grants, disk, or whether
  every table is writable.
- **2-second statement timeout** via `SET LOCAL`: a hung primary fails
  readiness slowly (up to the timeout) rather than instantly; it does not bound
  connect time (`connect_timeout=5`).
- **Failover orchestration is out of scope**: no automatic re-pointing of
  `DATABASE_HOST`, no WAL/replay-based promotion, no quorum logic.
- Requires a **fresh connection with no open transaction**; it raises if called
  inside one, so it cannot be dropped into the middle of an application
  transaction.
- Kubernetes users should combine this with the writer-only endpoint described
  in [Kafka lag autoscaling](kafka-autoscaling.md) — the KEDA manifests and the
  app must agree on which endpoint is the writer.
