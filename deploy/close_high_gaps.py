"""Close HIGH AgentCore gaps: memory strategies, registry retry, Lambda ping, IAM."""
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
    return p.returncode, data, (p.stderr or p.stdout or "").replace("\n", " ")[:900]


def main() -> int:
    ids = json.loads(OUT.read_text(encoding="utf-8"))
    print("=== runtime IAM ===")
    code, _, msg = aws_json(
        [
            "iam",
            "put-role-policy",
            "--role-name",
            "CreDealRentrollAgentCoreRuntime",
            "--policy-name",
            "agentcore-exec",
            "--policy-document",
            f"file://{DEPLOY / 'agentcore-exec-policy.json'}",
        ]
    )
    print("iam", code, msg[:200])

    print("=== get-memory ===")
    mid = ids["MEMORY_ID"]
    code, mem, msg = aws_json(["bedrock-agentcore-control", "get-memory", "--memory-id", mid])
    strategies = ((mem or {}).get("memory") or mem or {}).get("strategies") or ((mem or {}).get("memory") or {}).get(
        "memoryStrategies"
    )
    print("memory status", ((mem or {}).get("memory") or mem or {}).get("status"), "strategies", strategies)

    strat = {
        "addMemoryStrategies": [
            {
                "semanticMemoryStrategy": {
                    "name": "semanticFacts",
                    "description": "CRE deal semantic LTM",
                    "namespaces": ["/cre/{actorId}/facts"],
                }
            },
            {
                "summaryMemoryStrategy": {
                    "name": "sessionSummaries",
                    "description": "CRE session summaries",
                    "namespaces": ["/cre/{actorId}/summaries/{sessionId}"],
                }
            },
        ]
    }
    strat_path = Path(os.environ["TEMP"]) / "mem-strat.json"
    strat_path.write_text(json.dumps(strat), encoding="utf-8")
    role = f"arn:aws:iam::{ACCOUNT}:role/CreDealRentrollAgentCoreRuntime"
    if not strategies:
        print("=== update-memory add strategies ===")
        code, data, msg = aws_json(
            [
                "bedrock-agentcore-control",
                "update-memory",
                "--memory-id",
                mid,
                "--memory-execution-role-arn",
                role,
                "--memory-strategies",
                f"file://{strat_path}",
            ]
        )
        print("update-memory", code, msg[:700])
        for _ in range(24):
            time.sleep(5)
            _, mem, _ = aws_json(["bedrock-agentcore-control", "get-memory", "--memory-id", mid])
            body = (mem or {}).get("memory") or mem or {}
            st = body.get("status")
            print("  wait", st)
            if st in {"ACTIVE", "READY"}:
                break

    print("=== create-registry once ===")
    if not ids.get("REGISTRY_ID"):
        code, data, msg = aws_json(
            ["bedrock-agentcore-control", "create-registry", "--registry-name", "cre_deal_rentroll"]
        )
        print("create-registry", code, msg[:500])
        if data:
            rec = data.get("registry") or data
            rid = rec.get("registryId") or data.get("registryId")
            if rid:
                ids["REGISTRY_ID"] = rid
    else:
        print("registry already", ids.get("REGISTRY_ID"))

    print("=== gateway resource policy ===")
    gw_pol = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "RuntimeInvokeGateway",
                "Effect": "Allow",
                "Principal": {"AWS": role},
                "Action": ["bedrock-agentcore:InvokeGateway", "bedrock-agentcore:InvokeGatewayForUser"],
                "Resource": ids.get("GATEWAY_ARN")
                or f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:gateway/{ids['GATEWAY_ID']}",
            }
        ],
    }
    gw_path = Path(os.environ["TEMP"]) / "gw-resource-pol.json"
    gw_path.write_text(json.dumps(gw_pol), encoding="utf-8")
    for cmd in (
        ["bedrock-agentcore-control", "put-resource-policy", "--resource-arn", ids.get("GATEWAY_ARN") or "", "--policy", f"file://{gw_path}"],
        ["bedrock-agentcore-control", "put-gateway-policy", "--gateway-identifier", ids["GATEWAY_ID"], "--policy-document", f"file://{gw_path}"],
        ["bedrock-agentcore-control", "update-gateway", "--gateway-identifier", ids["GATEWAY_ID"], "--role-arn", f"arn:aws:iam::{ACCOUNT}:role/CreDealRentrollGateway"],
    ):
        if not cmd[-1] or cmd[-1].endswith(":"):
            continue
        code, data, msg = aws_json(cmd)
        print(cmd[1], code, msg[:240])
        if code == 0:
            break

    print("=== lambda cre_ping (creates log group) ===")
    payload_path = Path(os.environ["TEMP"]) / "cre-ping.json"
    out_path = Path(os.environ["TEMP"]) / "cre-ping-out.json"
    payload_path.write_text(json.dumps({"name": "cre_ping", "arguments": {"username": "burak"}}), encoding="utf-8")
    code, data, msg = aws_json(
        [
            "lambda",
            "invoke",
            "--function-name",
            "cre-deal-rentroll-gateway",
            "--cli-binary-format",
            "raw-in-base64-out",
            "--payload",
            f"file://{payload_path}",
            str(out_path),
        ]
    )
    body = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
    print("lambda", code, body[:300], msg[:200])

    OUT.write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
