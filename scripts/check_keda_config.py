"""Offline manifest contract checks; not Kubernetes schema/live-cluster validation.

Run: uv run --no-project --with pyyaml python scripts/check_keda_config.py
Requires kubectl (only its local kustomize renderer is invoked).
"""

import ast
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]
rendered = subprocess.check_output(["kubectl", "kustomize", str(ROOT / "deploy/keda")], text=True)
objects = list(yaml.safe_load_all(rendered))
deployments = {o["metadata"]["name"]: o for o in objects if o["kind"] == "Deployment"}
scalers = [o for o in objects if o["kind"] == "ScaledObject"]
config = next(o["data"] for o in objects if o["kind"] == "ConfigMap")
assert len(deployments) == len(scalers) == 3
source = ast.parse((ROOT / "consumer/app.py").read_text())
groups = {
    node.args[0].values[-1].value
    for node in ast.walk(source)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name)
    and node.func.id == "create_consumer"
    and isinstance(node.args[0], ast.JoinedStr)
}
assert groups == {"-logs", "-events", "-creates"}
consumer_budget = 0
for scaler in scalers:
    spec = scaler["spec"]
    deployment = deployments[spec["scaleTargetRef"]["name"]]
    assert scaler["metadata"]["namespace"] == deployment["metadata"]["namespace"] == "quadrourl"
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    env = {item["name"]: item["value"] for item in container["env"]}
    kind = env["CONSUMER_TYPE"]
    trigger = spec["triggers"][0]
    metadata = trigger["metadata"]
    topic_key = {
        "logs": "KAFKA_TOPIC_REQUEST_LOGS",
        "events": "KAFKA_TOPIC_URL_EVENTS",
        "creates": "KAFKA_TOPIC_URL_CREATES",
    }[kind]
    assert trigger["type"] == "kafka"
    assert metadata["consumerGroup"] == config["KAFKA_GROUP"] + "-" + kind
    assert metadata["topic"] == config[topic_key]
    assert metadata["bootstrapServers"] == config["KAFKA_BROKER"]
    assert metadata["offsetResetPolicy"] == "earliest"
    assert metadata["allowIdleConsumers"] == "false"
    assert all(isinstance(value, str) for value in metadata.values())
    assert (
        1 == spec["minReplicaCount"] <= spec["fallback"]["replicas"] <= spec["maxReplicaCount"] == 3
    )
    assert "replicas" not in deployment["spec"]
    assert deployment["spec"]["strategy"]["rollingUpdate"]["maxSurge"] == 0
    assert pod["terminationGracePeriodSeconds"] >= 90
    assert container["envFrom"][1]["secretRef"]["name"] == "consumer-database"
    assert "consumer-" + kind + ".heartbeat" in container["livenessProbe"]["exec"]["command"][-1]
    assert (
        spec["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]["scaleDown"][
            "stabilizationWindowSeconds"
        ]
        == 300
    )
    consumer_budget += spec["maxReplicaCount"] * int(env["DB_MAX_CONNECTIONS_" + kind.upper()])
assert consumer_budget == 75
print(
    "KEDA rendered contract checks passed: 3 deployments/scalers, groups/topics, 75 DB connections"
)
