#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$SCRIPT_DIR/.."
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$TF_DIR"

LOADTESTER_IP=$(terraform output -raw loadtester_public_ip)
ALB_DNS=$(terraform output -raw alb_dns_name)

echo "=== Load Test Configuration ==="
echo "  Load Tester IP: $LOADTESTER_IP"
echo "  ALB DNS:        $ALB_DNS"
echo ""

echo "=== Copying Locust Script to Load Tester ==="
scp -o StrictHostKeyChecking=no "$PROJECT_ROOT/scripts/test_locust.py" ec2-user@"$LOADTESTER_IP":/home/ec2-user/

echo ""
echo "=== Launching Headless Locust Test ==="
ssh -o StrictHostKeyChecking=no ec2-user@"$LOADTESTER_IP" << EOF
locust -f /home/ec2-user/test_locust.py \
  --host http://$ALB_DNS \
  --headless \
  -u 5000 \
  -r 200 \
  --run-time 10m \
  --csv=results_aws
EOF

echo ""
echo "=== Downloading Results ==="
mkdir -p "$PROJECT_ROOT/loadtests/results_aws_$(date +%Y%m%d_%H%M%S)"
RESULT_DIR="$PROJECT_ROOT/loadtests/results_aws_$(date +%Y%m%d_%H%M%S)"
scp -o StrictHostKeyChecking=no ec2-user@"$LOADTESTER_IP":/home/ec2-user/results_aws* "$RESULT_DIR/"

echo "=== Results saved to: $RESULT_DIR ==="
