#!/usr/bin/env bash
# Roll the AgentCore runtime onto a new image. Env vars are read back and
# written again so a deploy does not drop keys that are already set.
set -euo pipefail

image="${1:?image uri}"
region="${AWS_REGION:-us-east-1}"
runtime_id="${AGENTCORE_RUNTIME_ID:-cre_deal_rentroll-Nh63z6GXmK}"
role_arn="${AGENTCORE_ROLE_ARN:-arn:aws:iam::226721156429:role/CreDealRentrollAgentCoreRuntime}"

envf="$(mktemp)"
art="$(mktemp)"
cleanup() { rm -f "$envf" "$art"; }
trap cleanup EXIT

aws bedrock-agentcore-control get-agent-runtime \
  --region "$region" \
  --agent-runtime-id "$runtime_id" \
  --query environmentVariables \
  --output json >"$envf"
jq -e 'type == "object" and length > 0' "$envf" >/dev/null

jq -n --arg uri "$image" '{containerConfiguration: {containerUri: $uri}}' >"$art"

aws bedrock-agentcore-control update-agent-runtime \
  --region "$region" \
  --agent-runtime-id "$runtime_id" \
  --agent-runtime-artifact "file://${art}" \
  --role-arn "$role_arn" \
  --network-configuration networkMode=PUBLIC \
  --protocol-configuration serverProtocol=HTTP \
  --environment-variables "file://${envf}" \
  --query status \
  --output text
rm -f "$envf" "$art"

for _ in $(seq 1 40); do
  st="$(aws bedrock-agentcore-control get-agent-runtime \
    --region "$region" \
    --agent-runtime-id "$runtime_id" \
    --query status \
    --output text)"
  echo "runtime ${st}"
  case "$st" in
    READY) exit 0 ;;
    *FAIL*) exit 1 ;;
  esac
  sleep 15
done

echo "timed out waiting for AgentCore runtime" >&2
exit 1
