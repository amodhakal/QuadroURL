#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TF_DIR="$SCRIPT_DIR/.."

echo "=== Step 1: Provision Infrastructure ==="
cd "$TF_DIR"
terraform init
terraform apply -auto-approve

echo ""
echo "=== Step 2: Login to ECR & Push Docker Image ==="
AWS_REGION=$(terraform output -raw aws_region 2>/dev/null || echo "us-east-1")
ECR_URL=$(terraform output -raw ecr_repository_url)

aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_URL"

docker build -t "$ECR_URL:latest" "$PROJECT_ROOT"
docker push "$ECR_URL:latest"

echo ""
echo "=== Step 3: Force ECS to Pick Up New Image ==="
CLUSTER_NAME=$(terraform output -raw alb_dns_name 2>/dev/null | cut -d'-' -f1-5 || echo "url-shortener-loadtest")
aws ecs update-service \
  --cluster "$CLUSTER_NAME" \
  --service "$CLUSTER_NAME" \
  --force-new-deployment \
  --region "$AWS_REGION" || echo "Note: Update service name if different"

echo ""
echo "=== Deployment Complete ==="
terraform output
