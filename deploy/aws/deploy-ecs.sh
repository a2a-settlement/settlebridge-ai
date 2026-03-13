#!/usr/bin/env bash
set -euo pipefail

# SettleBridge Gateway -- AWS ECS Fargate Deployment
# Usage: ./deploy-ecs.sh --region us-east-1 --domain gateway.example.com --cert-arn arn:aws:acm:...

STACK_NAME="settlebridge-gateway"
REGION="us-east-1"
DOMAIN=""
CERT_ARN=""
GATEWAY_IMAGE=""
DASHBOARD_IMAGE=""
DB_INSTANCE_CLASS="db.t3.micro"
CACHE_NODE_TYPE="cache.t3.micro"

while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --cert-arn) CERT_ARN="$2"; shift 2 ;;
    --gateway-image) GATEWAY_IMAGE="$2"; shift 2 ;;
    --dashboard-image) DASHBOARD_IMAGE="$2"; shift 2 ;;
    --db-instance) DB_INSTANCE_CLASS="$2"; shift 2 ;;
    --cache-node) CACHE_NODE_TYPE="$2"; shift 2 ;;
    --stack-name) STACK_NAME="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ -z "$GATEWAY_IMAGE" || -z "$DASHBOARD_IMAGE" ]]; then
  echo "ERROR: --gateway-image and --dashboard-image are required"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Deploying SettleBridge Gateway to AWS ECS Fargate"
echo "  Region:     $REGION"
echo "  Stack:      $STACK_NAME"
echo "  Domain:     ${DOMAIN:-none}"
echo "  Gateway:    $GATEWAY_IMAGE"
echo "  Dashboard:  $DASHBOARD_IMAGE"

aws cloudformation deploy \
  --template-file "$SCRIPT_DIR/cloudformation.yaml" \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    GatewayImage="$GATEWAY_IMAGE" \
    DashboardImage="$DASHBOARD_IMAGE" \
    DomainName="$DOMAIN" \
    CertificateArn="$CERT_ARN" \
    DBInstanceClass="$DB_INSTANCE_CLASS" \
    CacheNodeType="$CACHE_NODE_TYPE" \
  --no-fail-on-empty-changeset

echo ""
echo "Fetching outputs..."
aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'Stacks[0].Outputs' \
  --output table

echo ""
echo "Deployment complete."
