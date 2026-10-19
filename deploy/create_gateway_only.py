from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

AWS = r"C:\Program Files\Amazon\AWSCLIV2\aws.exe"
REGION = "us-east-1"
ACCOUNT = "226721156429"
DEPLOY = Path(__file__).resolve().parent
OUT = DEPLOY / "agentcore-platform.json"


def aws_json(args):
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
    return p.returncode, data, (p.stderr or p.stdout or "").replace("\n", " ")[:800]


def main():
    ids = json.loads(OUT.read_text(encoding="utf-8"))
    proto = {"mcp": {"supportedVersions": ["2025-03-26"], "instructions": "CRE rent-roll MCP tools"}}
    proto_path = Path(os.environ["TEMP"]) / "gw-proto.json"
    proto_path.write_text(json.dumps(proto), encoding="utf-8")
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
        "--policy-engine-configuration",
        f"arn={ids['POLICY_ENGINE_ARN']},mode=LOG_ONLY",
    ]
    code, data, msg = aws_json(args)
    print("gw", code, msg[:700])
    if code != 0:
        args = args[:-2]
        code, data, msg = aws_json(args)
        print("gw no-policy", code, msg[:700])
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
        print("target", code, msg[:700])
        _, data, _ = aws_json(["bedrock-agentcore-control", "get-gateway", "--gateway-identifier", ids["GATEWAY_ID"]])
        g = (data or {}).get("gateway") or (data or {})
        ids["GATEWAY_URL"] = ids.get("GATEWAY_URL") or g.get("gatewayUrl")
        ids["GATEWAY_ARN"] = ids.get("GATEWAY_ARN") or g.get("gatewayArn")
    OUT.write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(ids, indent=2))


if __name__ == "__main__":
    main()
