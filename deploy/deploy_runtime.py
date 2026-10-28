"""Attach Policy to Gateway and update AgentCore Runtime env (no secrets printed)."""
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
    return p.returncode, data, (p.stderr or p.stdout or "").replace("\n", " ")[:800]


def main() -> int:
    ids = json.loads(OUT.read_text(encoding="utf-8"))
    ids["GATEWAY_TARGET_ID"] = ids.get("GATEWAY_TARGET_ID") or "QZAHMOBLU1"
    ids.pop("PAYMENT_MANAGER_ARN", None)
    ids.pop("PAYMENT_MANAGER_ID", None)

    print("=== gateway IAM ===")
    code, _, msg = aws_json(
        [
            "iam",
            "put-role-policy",
            "--role-name",
            "CreDealRentrollGateway",
            "--policy-name",
            "gateway",
            "--policy-document",
            f"file://{DEPLOY / 'gateway-role-policy.json'}",
        ]
    )
    print("iam", code)

    print("=== runtime exec IAM ===")
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
    print("runtime-iam", code, msg[:200] if code else "ok")

    print("=== runtime spans log resource policy ===")
    code, _, msg = aws_json(
        [
            "logs",
            "put-resource-policy",
            "--policy-name",
            "CreDealRentrollRuntimeSpansXRay",
            "--policy-document",
            f"file://{DEPLOY / 'runtime-spans-resource-policy.json'}",
        ]
    )
    print("log-resource-policy", code, msg[:300] if code else "ok")

    print("=== update-gateway policy engine ===")
    code, data, msg = aws_json(
        [
            "bedrock-agentcore-control",
            "update-gateway",
            "--gateway-identifier",
            ids["GATEWAY_ID"],
            "--role-arn",
            f"arn:aws:iam::{ACCOUNT}:role/CreDealRentrollGateway",
            "--policy-engine-configuration",
            f"arn={ids['POLICY_ENGINE_ARN']},mode=LOG_ONLY",
        ]
    )
    print("update-gateway", code, msg[:500])

    print("=== get runtime (redacted) ===")
    code, rt, msg = aws_json(
        ["bedrock-agentcore-control", "get-agent-runtime", "--agent-runtime-id", "cre_deal_rentroll-Nh63z6GXmK"]
    )
    env = dict((rt or {}).get("environmentVariables") or {})
    print("runtime status", (rt or {}).get("status"), "env keys", sorted(k for k in env if "KEY" not in k))
    extra = {
        "AGENTCORE_INSIDE": "1",
        "AWS_REGION": "us-east-1",
        "DDB_TABLE": "ml_interview",
        "AGENTCORE_WORKLOAD_NAME": ids["WORKLOAD_NAME"],
        "AGENTCORE_IDENTITY_NAME": ids.get("IDENTITY_NAME") or "cre_deal_rentroll_wli",
        "AGENTCORE_MEMORY_ID": ids["MEMORY_ID"],
        "AGENTCORE_GATEWAY_URL": ids["GATEWAY_URL"],
        "AGENTCORE_GATEWAY_ID": ids["GATEWAY_ID"],
        "AGENTCORE_HARNESS_ID": ids["HARNESS_ID"],
        "AGENTCORE_EVALUATOR_ID": ids["EVALUATOR_ID"],
        "AGENTCORE_ONLINE_EVAL_ID": ids["ONLINE_EVAL_ID"],
        "AGENTCORE_POLICY_ENGINE_ID": ids["POLICY_ENGINE_ID"],
        "AGENTCORE_POLICY_ID": ids["POLICY_ID"],
        "AGENTCORE_CODE_INTERPRETER_ID": ids["CODE_INTERPRETER_ID"],
        "AGENTCORE_BROWSER_ID": ids["BROWSER_ID"],
        "AGENT_OBSERVABILITY_ENABLED": "true",
        "UNIFIED_TRACES_DESTINATION_ENABLED": "true",
        "RR_PREDICT_URL": "https://vacancy-agent.purplegrass-79f82a02.eastus.azurecontainerapps.io",
    }
    env.update(extra)
    env.pop("CRE_SKILLS_DIR", None)
    env.pop("AGENTCORE_PAYMENT_MANAGER_ARN", None)
    env.pop("AGENTCORE_PAYMENT_MANAGER_ID", None)
    if ids.get("REGISTRY_ID"):
        env["AGENTCORE_REGISTRY_ID"] = ids["REGISTRY_ID"]

    env_path = Path(os.environ["TEMP"]) / "runtime-env.json"
    env_path.write_text(json.dumps(env), encoding="utf-8")
    artifact = {
        "containerConfiguration": {
            "containerUri": "226721156429.dkr.ecr.us-east-1.amazonaws.com/cre-deal-rentroll-agentcore:latest"
        }
    }
    art_path = Path(os.environ["TEMP"]) / "runtime-art.json"
    art_path.write_text(json.dumps(artifact), encoding="utf-8")

    print("=== update-agent-runtime env ===")
    code, data, msg = aws_json(
        [
            "bedrock-agentcore-control",
            "update-agent-runtime",
            "--agent-runtime-id",
            "cre_deal_rentroll-Nh63z6GXmK",
            "--agent-runtime-artifact",
            f"file://{art_path}",
            "--role-arn",
            f"arn:aws:iam::{ACCOUNT}:role/CreDealRentrollAgentCoreRuntime",
            "--network-configuration",
            "networkMode=PUBLIC",
            "--protocol-configuration",
            "serverProtocol=HTTP",
            "--environment-variables",
            f"file://{env_path}",
        ]
    )
    print("update-agent-runtime", code, msg[:500])
    if data:
        print("new version", data.get("agentRuntimeVersion") or (data.get("agentRuntime") or {}).get("agentRuntimeVersion"))

    print("=== apprunner describe (redacted) ===")
    code, svc, msg = aws_json(
        [
            "apprunner",
            "describe-service",
            "--service-arn",
            "arn:aws:apprunner:us-east-1:226721156429:service/cre-deal-rentroll/e02e2828860543edbb0e3a23f6d92ebb",
        ]
    )
    cfg = (((svc or {}).get("Service") or {}).get("SourceConfiguration") or {}).get("ImageRepository") or {}
    img_cfg = cfg.get("ImageConfiguration") or {}
    runtime_env = img_cfg.get("RuntimeEnvironmentVariables") or {}
    print("apprunner env keys", sorted(k for k in runtime_env if "KEY" not in k and "SECRET" not in k))
    print("AGENTCORE_RUNTIME_ARN set", bool(runtime_env.get("AGENTCORE_RUNTIME_ARN")))

    OUT.write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
