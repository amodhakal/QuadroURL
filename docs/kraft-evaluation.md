# KRaft evaluation (#155)

## Decision

Keep the ZooKeeper Compose default for existing installations. Offer
`docker-compose.kraft.yml` only for a **fresh, disposable local cluster**. It uses
the same `confluentinc/cp-kafka:7.6.0` as the default so this evaluates metadata
mode, not a simultaneous Kafka upgrade. KRaft removes the ZooKeeper process and
its independent availability/upgrade burden. A single combined broker/controller
is useful locally, but neither this nor the single-ZooKeeper default is HA.
Production adoption needs isolated controllers (normally three), replicated
brokers, TLS/auth, monitoring, backups and a supported Kafka release plan.

**This is not an in-place migration overlay.** Do not attach existing ZooKeeper
broker disks to it or enable it against production. Migration requires the
version-specific Confluent migration procedure, controller quorum provisioning,
metadata compatibility checks and a rehearsed rollback window before finalizing.
Alternatively use a new cluster plus an explicitly managed topic/data/offset
transfer and client cutover. Consumer group offsets do not transfer simply by
reusing a group name. Neither migration path is implemented or validated here.

## Fresh local deployment

Requires Docker Compose >= 2.24.4 (`!reset` / `!override`) and a Docker daemon.
Commands run from the repository root. The separate project name isolates named
volumes/network, but published ports in the base file still conflict with another
running full Compose project. For comparison run **only Kafka** first:

```sh
# If missing, create .env from .env.example, then edit credentials/settings.
# Never overwrite an existing .env. Set GF_ADMIN_PASSWORD in .env too;
# base Compose requires it even when starting only Kafka.
test -f .env || cp .env.example .env
export KRAFT_CLUSTER_ID=MkU3OEVBNTcwNTJENDM2Qk
# Keep this ID stable for the lifetime of the kraft volume.
docker compose -p quadrourl-kraft -f docker-compose.yml -f docker-compose.kraft.yml config --quiet
docker compose -p quadrourl-kraft -f docker-compose.yml -f docker-compose.kraft.yml up -d kafka
docker compose -p quadrourl-kraft -f docker-compose.yml -f docker-compose.kraft.yml exec kafka \
  kafka-metadata-quorum --bootstrap-server kafka:9092 describe --status
docker compose -p quadrourl-kraft -f docker-compose.yml -f docker-compose.kraft.yml exec kafka \
  kafka-topics --bootstrap-server kafka:9092 --create --if-not-exists \
  --topic kraft-smoke --partitions 3 --replication-factor 1
printf 'kraft-smoke-message\n' | docker compose -p quadrourl-kraft -f docker-compose.yml -f docker-compose.kraft.yml exec -T kafka \
  kafka-console-producer --bootstrap-server kafka:9092 --topic kraft-smoke
docker compose -p quadrourl-kraft -f docker-compose.yml -f docker-compose.kraft.yml exec -T kafka \
  kafka-console-consumer --bootstrap-server kafka:9092 --topic kraft-smoke \
  --from-beginning --max-messages 1 --timeout-ms 10000
```

After freeing the base stack's published ports, start the rest with the same
project/file arguments and `up -d --build`. Run application smoke tests against
that isolated stack. Broker advertising is internal (`kafka:9092`); a host client
cannot use it without an additional listener. ZooKeeper is excluded by profile;
do not enable the `legacy-zookeeper` profile for this evaluation.

Restart Kafka with those same arguments and verify quorum status and the smoke
message survive. The `kafka-kraft-data` named volume persists log **and metadata**
state; keep its cluster ID unchanged. `down` (without `-v`) stops the evaluation
and retains volumes. To return to the legacy stack, stop the evaluation, then
start the original project using only `docker-compose.yml`. This switches
clusters, **not** a data-preserving rollback of new writes.

## Validation and promotion gates

Local static check (no daemon, secrets or .env required):

```sh
GF_ADMIN_PASSWORD=config-check-only docker compose --env-file .env.example -p quadrourl-kraft \
  -f docker-compose.yml -f docker-compose.kraft.yml \
  config --no-env-resolution --format json
```

Verify Kafka has no `depends_on` or `KAFKA_ZOOKEEPER_CONNECT`, has controller
quorum/listener settings, and mounts only the new Kraft data volume. The default
Compose file must still include ZooKeeper. Configuration rendering was checked;
no live startup, message delivery, persistence/restart, throughput comparison or
migration was performed during implementation. Record those results before
promoting this option beyond a local evaluation.

References: [Confluent KRaft configuration](https://docs.confluent.io/platform/7.6/installation/docker/config-reference.html#kafka-configuration),
[ZooKeeper-to-KRaft migration](https://docs.confluent.io/platform/7.6/installation/migrate-zk-kraft.html).
