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
echo "=== Step 2: Login to ECR & Push Docker Images ==="
AWS_REGION=$(terraform output -raw aws_region 2>/dev/null || echo "us-east-1")
ECR_URL=$(terraform output -raw ecr_repository_url)
ECR_CONSUMER_URL=$(terraform output -raw ecr_consumer_repository_url)

aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_URL"

docker build -t "$ECR_URL:latest" "$PROJECT_ROOT"
docker push "$ECR_URL:latest"

docker build -t "$ECR_CONSUMER_URL:latest" "$PROJECT_ROOT/consumer"
docker push "$ECR_CONSUMER_URL:latest"

echo ""
echo "=== Step 3: Force ECS to Pick Up New Images ==="
CLUSTER_NAME=$(terraform output -raw cluster_name)
APP_SERVICE=$(terraform output -raw app_service_name)

aws ecs update-service \
  --cluster "$CLUSTER_NAME" \
  --service "$APP_SERVICE" \
  --force-new-deployment \
  --region "$AWS_REGION"

for service in request-log url-event url-create; do
  aws ecs update-service \
    --cluster "$CLUSTER_NAME" \
    --service "$service" \
    --force-new-deployment \
    --region "$AWS_REGION"
done

echo ""
echo "=== Note: Kafka bootstraps via EC2 user_data and may take a few minutes ==="
echo "=== to be ready; consumers reconnect automatically once the broker is up ==="

echo ""
echo "=== Deployment Complete ==="
terraform output
