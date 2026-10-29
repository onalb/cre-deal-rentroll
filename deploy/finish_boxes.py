"""Create Gateway with supported MCP version, retry Payments, persist ARNs."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

AWS = r"C:\Program Files\Amazon\AWSCLIV2\aws.exe"
REGION = "us-east-1"
ACCOUNT = "226721156429"
DEPLOY = Path(__file__).resolve().parent
OUT = DEPLOY / "agentcore-platform.json"
os.environ.setdefault("AWS_PROFILE", "default")
os.environ.setdefault("AWS_DEFAULT_REGION", REGION)
os.environ.setdefault("AWS_PAGER", "")


def aws_json(args: list[str]) -> tuple[int, dict | None, str]:
    p = subprocess.run(
        [AWS, *args, "--region", REGION, "--output", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    data = None
    if p.stdout.strip():
        try:
            data = json.loads(p.stdout)
        except json.JSONDecodeError:
            data = None
    msg = (p.stderr or p.stdout or "").replace("\n", " ")[:800]
    return p.returncode, data, msg


def main() -> int:
    ids = json.loads(OUT.read_text(encoding="utf-8"))
    proto = {"mcp": {"supportedVersions": ["2025-03-26"], "instructions": "CRE rent-roll MCP tools"}}
    proto_path = Path(os.environ["TEMP"]) / "gw-proto.json"
    proto_path.write_text(json.dumps(proto), encoding="utf-8")

    print("=== create-gateway ===")
    args = [
        "bedrock-agentcore-control",
        "create-gateway",
        "--name",
        "credealrentroll",
        "--role-arn",
        f"arn:aws:iam::{ACCOUNT}:role/CreDealRentrollGateway",
        "--protocol-type",
        "MCP",
        "--authorizer-type",
        "AWS_IAM",
        "--protocol-configuration",
        f"file://{proto_path}",
    ]
    code, data, msg = aws_json(args)
    print("gw", code, msg[:600])
    if data:
        g = data.get("gateway") or data
        ids["GATEWAY_ID"] = g.get("gatewayId") or data.get("gatewayId")
        ids["GATEWAY_ARN"] = g.get("gatewayArn") or data.get("gatewayArn")
        ids["GATEWAY_URL"] = g.get("gatewayUrl") or data.get("gatewayUrl")

    if ids.get("GATEWAY_ID"):
        tools = json.loads((DEPLOY / "gateway-tools.json").read_text(encoding="utf-8"))
        target = {"mcp": {"lambda": {"lambdaArn": ids["LAMBDA_ARN"], "toolSchema": {"inlinePayload": tools}}}}
        tc = Path(os.environ["TEMP"]) / "gw-target.json"
        cred = Path(os.environ["TEMP"]) / "gw-cred.json"
        tc.write_text(json.dumps(target), encoding="utf-8")
        cred.write_text(json.dumps([{"credentialProviderType": "GATEWAY_IAM_ROLE"}]), encoding="utf-8")
        code, data, msg = aws_json(
            [
                "bedrock-agentcore-control",
                "create-gateway-target",
                "--gateway-identifier",
                ids["GATEWAY_ID"],
                "--name",
                "cretools",
                "--target-configuration",
                f"file://{tc}",
                "--credential-provider-configurations",
                f"file://{cred}",
            ]
        )
        print("target", code, msg[:600])
        _, data, _ = aws_json(["bedrock-agentcore-control", "get-gateway", "--gateway-identifier", ids["GATEWAY_ID"]])
        g = (data or {}).get("gateway") or (data or {})
        ids["GATEWAY_URL"] = ids.get("GATEWAY_URL") or g.get("gatewayUrl")
        ids["GATEWAY_ARN"] = ids.get("GATEWAY_ARN") or g.get("gatewayArn")
        print("gateway url", ids.get("GATEWAY_URL"))

    print("=== payment manager ===")
    time.sleep(8)
    code, data, msg = aws_json(
        [
            "bedrock-agentcore-control",
            "create-payment-manager",
            "--name",
            "CreDealRentrollPay",
            "--authorizer-type",
            "AWS_IAM",
            "--role-arn",
            f"arn:aws:iam::{ACCOUNT}:role/CreDealRentrollAgentCoreRuntime",
        ]
    )
    print("pay", code, msg[:600])
    if data:
        pm = data.get("paymentManager") or data
        ids["PAYMENT_MANAGER_ARN"] = pm.get("paymentManagerArn") or data.get("paymentManagerArn")
        ids["PAYMENT_MANAGER_ID"] = pm.get("paymentManagerId") or data.get("paymentManagerId")

    _, data, _ = aws_json(["bedrock-agentcore-control", "get-harness", "--harness-id", ids["HARNESS_ID"]])
    h = (data or {}).get("harness") or {}
    ids["HARNESS_ARN"] = h.get("arn")
    env = (h.get("environment") or {}).get("agentCoreRuntimeEnvironment") or {}
    ids["HARNESS_RUNTIME_ARN"] = env.get("agentRuntimeArn")
    ids["API_KEY_PROVIDER_ARN"] = (
        "arn:aws:bedrock-agentcore:us-east-1:226721156429:token-vault/default/apikeycredentialprovider/cre-deal-rentroll-openai"
    )
    ids["ONLINE_EVAL_ARN"] = (
        "arn:aws:bedrock-agentcore:us-east-1:226721156429:online-evaluation-config/cre_deal_rentroll_online-Y9tvcwAgAk"
    )
    ids.pop("POLICY_ENGINE_ID", None)
    ids.pop("POLICY_ENGINE_ARN", None)
    ids.pop("POLICY_ID", None)
    ids.pop("POLICY_ARN", None)
    OUT.write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(ids, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
