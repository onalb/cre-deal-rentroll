#!/usr/bin/env bash
# Point an existing App Runner service at a new ECR image.
# Copies the current source configuration and changes only the image URI,
# so runtime env vars and secrets stay as they are.
set -euo pipefail

arn="${1:?service arn}"
image="${2:?image uri}"
region="${AWS_REGION:-us-east-1}"

svc="$(mktemp)"
src="$(mktemp)"
cleanup() { rm -f "$svc" "$src"; }
trap cleanup EXIT

aws apprunner describe-service --region "$region" --service-arn "$arn" --output json >"$svc"
jq -e --arg img "$image" '
  .Service.SourceConfiguration
  | .ImageRepository.ImageIdentifier = $img
  | {
      ImageRepository: .ImageRepository,
      AutoDeploymentsEnabled: .AutoDeploymentsEnabled,
      AuthenticationConfiguration: .AuthenticationConfiguration
    }
' "$svc" >"$src"
rm -f "$svc"

op="$(aws apprunner update-service \
  --region "$region" \
  --service-arn "$arn" \
  --source-configuration "file://${src}" \
  --query OperationId \
  --output text)"
rm -f "$src"
echo "apprunner operation ${op}"

for _ in $(seq 1 60); do
  st="$(aws apprunner list-operations \
    --region "$region" \
    --service-arn "$arn" \
    --max-results 10 \
    --query "OperationSummaryList[?Id=='${op}'].Status | [0]" \
    --output text)"
  echo "apprunner ${st}"
  case "$st" in
    SUCCEEDED)
      aws apprunner describe-service \
        --region "$region" \
        --service-arn "$arn" \
        --query Service.ServiceUrl \
        --output text
      exit 0
      ;;
    FAILED|ROLLBACK_FAILED|ROLLBACK_SUCCEEDED)
      echo "App Runner did not keep the new image (${st})" >&2
      exit 1
      ;;
  esac
  sleep 15
done

echo "timed out waiting for App Runner" >&2
exit 1
