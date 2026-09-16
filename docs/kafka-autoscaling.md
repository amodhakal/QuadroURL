# Kafka lag autoscaling (#186)

`deploy/keda` is an opt-in Kubernetes consumer deployment, not a Compose
extension or a complete Kubernetes platform. It deploys three single-process
workers and three KEDA Kafka ScaledObjects. Existing Compose replicas are unchanged.

| Deployment | Actual consumer group | Topic | Target lag per replica |
|---|---|---|---|
| request-log-consumer | request-log-writer-logs | request-logs | 1000 |
| url-event-consumer | request-log-writer-events | url-events | 500 |
| url-create-consumer | request-log-writer-creates | url-creates | 100 |

The worker appends the type suffix to `KAFKA_GROUP`; KEDA must use the full name.
`earliest` matches worker offset reset behavior. Initial thresholds match batch
sizes, **not measured throughput**: tune using lag age, drain duration and DB load.
Each group scales 1–3 pods, bounded by available topic partitions, with no idle
consumers. Explicit topics and a floor of one avoid a new group's zero-offset
bootstrap trap. No scale-to-zero is configured. HPA scale-down stabilization is
300 seconds (KEDA cooldown alone would only govern scaling to zero); changes are
limited to one pod/minute down and one pod/30 seconds up. After three scaler
failures fallback targets one pod, not a guarantee that lag can be cleared.

## Prerequisites and installation

Use a non-production cluster first. Install a platform-approved KEDA version
supporting the [2.18 Kafka scaler contract](https://keda.sh/docs/2.18/scalers/apache-kafka/)
and autoscaling/v2. For a reproducible evaluation installation (review release
support/security before production):

```sh
helm repo add kedacore https://kedacore.github.io/charts
helm repo update kedacore
helm upgrade --install keda kedacore/keda --namespace keda --create-namespace --version 2.18.0 --wait
kubectl wait --for=condition=Established crd/scaledobjects.keda.sh --timeout=120s
kubectl apply -f deploy/keda/namespace.yaml
```

Provision Kafka, Redis and a **writer-only** PostgreSQL endpoint separately.
Defaults expect Services in namespace `quadrourl` named `kafka:9092`, `redis:6379`
and `postgres-writer:5432`. Kafka must advertise addresses reachable from both
consumer pods and the KEDA operator (not Compose-only DNS). Create `request-logs`,
`url-events`, and `url-creates` with three partitions; use a replication factor
supported by your broker count. Apply the application schema/migrations before
starting consumers. Provide DB credentials without checking secrets into Git:

```sh
# Populate a private file outside the repo with DATABASE_USER and DATABASE_PASSWORD.
# If Redis requires a password, include REDIS_URL here to override the ConfigMap.
chmod 600 /secure/path/consumer-database.env
kubectl -n quadrourl create secret generic consumer-database \
  --from-env-file=/secure/path/consumer-database.env --dry-run=client -o yaml | kubectl apply -f -
```

Build from the **repository root** (the image includes `shared/`), push to a
registry your cluster can pull from, then edit `deploy/keda/kustomization.yaml`
to replace the deliberately non-runnable `example.invalid/...:replace-me` image
with that repository and immutable digest (`digest: sha256:...`, remove `newTag`):

```sh
export CONSUMER_IMAGE=your-registry.example/team/quadrourl-consumer
export IMAGE_TAG=$(git rev-parse HEAD)
docker build -f consumer/Dockerfile -t "$CONSUMER_IMAGE:$IMAGE_TAG" .
docker push "$CONSUMER_IMAGE:$IMAGE_TAG"
# Use the digest from push in kustomization.yaml; configure imagePullSecrets if needed.
```

Edit ConfigMap literals for real endpoints/database. If changing the Kafka broker,
topic or group prefix, update **all matching trigger metadata** in
`scaledobjects.yaml` too. The provided Kafka transport is plaintext on a trusted
private network, matching the current worker. This is not an authenticated-public
Kafka deployment: adding KEDA TriggerAuthentication alone is insufficient; worker
Kafka client/producer TLS/SASL configuration must also be implemented before using
such a broker. Apply network policy appropriate to the platform.

```sh
uv run --no-project --with pyyaml python scripts/check_keda_config.py
kubectl kustomize deploy/keda
# This next check DOES require a cluster, KEDA CRDs and admission webhooks:
kubectl apply --dry-run=server -k deploy/keda
kubectl apply -k deploy/keda
kubectl -n quadrourl rollout status deployment/request-log-consumer --timeout=180s
kubectl -n quadrourl rollout status deployment/url-event-consumer --timeout=180s
kubectl -n quadrourl rollout status deployment/url-create-consumer --timeout=180s
kubectl -n quadrourl get scaledobjects,hpa,deployments
```

Avoid running both old and new consumers on the same group except during a
planned handover: KEDA only controls its Kubernetes pods, not external members.
Stop old workers gracefully before evaluating replica/partition behavior.
Do not attach another HPA to these Deployments. Replica fields are omitted so
re-applying manifests does not reset KEDA's decision.

## Capacity, draining and operational acceptance

At max replicas these consumers reserve `3*(10+10+5)=75` DB connections; with the
current API budget of 240, total is 315 (see [capacity.md](capacity.md)). Recalculate
for **all regions, old workers, failover overlap and rolling replacements** before
raising either partitions or max replicas. `maxSurge: 0` reduces rollout overlap;
terminating pods can still hold connections, so reserve headroom. Ninety seconds
of termination grace allows the existing SIGTERM final drain/commit/close path;
it does not guarantee completion during a DB stall. Forced termination/rebalances
can replay records; this change does not establish exactly-once delivery.
Heartbeat probes indicate a running loop, not DB progress, so monitor commit lag
and DB errors independently.

In staging, record initial group offsets; produce valid traffic through the API;
observe lag crossing each threshold, replicas rising (at most three), offsets
advancing, and replicas falling to one after stabilization. Observe a rolling
restart and SIGTERM with buffered records. Compare message IDs/counts for loss or
replay. Interrupt broker access for the scaler and observe fallback/recovery.
Check `kubectl -n quadrourl describe scaledobject <name>` and `describe hpa` for
metric errors. A hot single partition cannot be fixed by more replicas; a DB
outage is not a capacity problem and scaling must not overload recovery.

To freeze autoscaling at one pod without deleting workers:

```sh
kubectl -n quadrourl annotate scaledobject request-log-consumer url-event-consumer url-create-consumer \
  autoscaling.keda.sh/paused-replicas="1" --overwrite
# Resume only after investigating:
kubectl -n quadrourl annotate scaledobject request-log-consumer url-event-consumer url-create-consumer \
  autoscaling.keda.sh/paused-replicas-
```

For rollback to manually managed replicas, delete **only** the three ScaledObjects
(`kubectl -n quadrourl delete scaledobject request-log-consumer url-event-consumer url-create-consumer`),
wait for their managed HPAs to disappear, then `kubectl -n quadrourl scale deployment
request-log-consumer url-event-consumer url-create-consumer --replicas=1`.
Do not use `kubectl delete -k deploy/keda`: it includes the namespace and could
delete unrelated stateful infrastructure/secrets there.

Validation performed: offline Kustomize rendering and contract assertions for
Deployment targets, group/topic/broker alignment, string metadata, bounds,
heartbeat paths and pool budget. No image build, server-side admission, Kafka/DB
connectivity, lag load test, rebalance/drain or live HPA action was verified.
