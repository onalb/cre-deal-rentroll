"""Retry remaining AgentCore boxes with corrected API shapes."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
OUT = DEPLOY / "agentcore-platform.json"
REGION = "us-east-1"
ACCOUNT = "226721156429"
AWS = r"C:\Program Files\Amazon\AWSCLIV2\aws.exe"
PY = sys.executable
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


def save(ids: dict) -> None:
    clean = {k: v for k, v in ids.items() if v and "KEY" not in k.upper()}
    OUT.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ids = json.loads(OUT.read_text(encoding="utf-8"))
    print("=== put runtime policy ===")
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
    print("  policy", code, msg[:120])

    print("=== lambda ===")
    zip_path = Path(tempfile.gettempdir()) / "cre-gw-lambda.zip"
    lam_dir = Path(tempfile.gettempdir()) / "cre-gw-lambda"
    if not zip_path.exists():
        if lam_dir.exists():
            shutil.rmtree(lam_dir, ignore_errors=True)
        lam_dir.mkdir()
        shutil.copy(DEPLOY / "lambda_gateway" / "handler.py", lam_dir / "handler.py")
        shutil.copytree(ROOT / "api" / "cre_rentroll", lam_dir / "cre_rentroll")
        subprocess.run(
            [PY, "-m", "pip", "install", "-q", "pydantic", "httpx", "ddgs", "openpyxl", "python-dotenv", "boto3", "-t", str(lam_dir)],
            check=False,
        )
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in lam_dir.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(lam_dir).as_posix())
    code, _, msg = aws_json(["lambda", "get-function", "--function-name", "cre-deal-rentroll-gateway"])
    if code != 0:
        time.sleep(5)
        code, data, msg = aws_json(
            [
                "lambda",
                "create-function",
                "--function-name",
                "cre-deal-rentroll-gateway",
                "--runtime",
                "python3.12",
                "--role",
                f"arn:aws:iam::{ACCOUNT}:role/CreDealRentrollLambda",
                "--handler",
                "handler.lambda_handler",
                "--timeout",
                "60",
                "--memory-size",
                "512",
                "--zip-file",
                f"fileb://{zip_path}",
                "--environment",
                "Variables={DDB_TABLE=ml_interview}",
            ]
        )
        print("  create-function", code, msg[:300])
    else:
        print("  lambda exists")
    aws_json(
        [
            "lambda",
            "add-permission",
            "--function-name",
            "cre-deal-rentroll-gateway",
            "--statement-id",
            "agentcore-gateway",
            "--action",
            "lambda:InvokeFunction",
            "--principal",
            "bedrock-agentcore.amazonaws.com",
        ]
    )
    ids["LAMBDA_ARN"] = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:cre-deal-rentroll-gateway"

    print("=== gateway ===")
    proto = {"mcp": {"supportedVersions": ["2023-06-01"], "instructions": "CRE rent-roll MCP tools"}}
    proto_path = Path(tempfile.gettempdir()) / "gw-proto.json"
    proto_path.write_text(json.dumps(proto), encoding="utf-8")
    gw_args = [
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
    code, data, msg = aws_json(gw_args)
    print("  create-gateway", code, msg[:500])
    if code != 0:
        gw_args = [
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
        code, data, msg = aws_json(gw_args)
        print("  create-gateway retry", code, msg[:500])
    if data:
        g = data.get("gateway") or data
        ids["GATEWAY_ID"] = g.get("gatewayId") or data.get("gatewayId")
        ids["GATEWAY_ARN"] = g.get("gatewayArn") or data.get("gatewayArn")
        ids["GATEWAY_URL"] = g.get("gatewayUrl") or data.get("gatewayUrl")
    if not ids.get("GATEWAY_ID"):
        _, listed, _ = aws_json(["bedrock-agentcore-control", "list-gateways"])
        for item in (listed or {}).get("items") or []:
            ids["GATEWAY_ID"] = item.get("gatewayId")
            ids["GATEWAY_ARN"] = item.get("gatewayArn")
            ids["GATEWAY_URL"] = item.get("gatewayUrl")
            break

    if ids.get("GATEWAY_ID") and not ids.get("GATEWAY_URL"):
        _, data, _ = aws_json(["bedrock-agentcore-control", "get-gateway", "--gateway-identifier", ids["GATEWAY_ID"]])
        g = (data or {}).get("gateway") or (data or {})
        ids["GATEWAY_URL"] = g.get("gatewayUrl")
        ids["GATEWAY_ARN"] = ids.get("GATEWAY_ARN") or g.get("gatewayArn")

    if ids.get("GATEWAY_ID"):
        tools = json.loads((DEPLOY / "gateway-tools.json").read_text(encoding="utf-8"))
        target = {"mcp": {"lambda": {"lambdaArn": ids["LAMBDA_ARN"], "toolSchema": {"inlinePayload": tools}}}}
        tc = Path(tempfile.gettempdir()) / "gw-target.json"
        cred = Path(tempfile.gettempdir()) / "gw-cred.json"
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
        print("  create-gateway-target", code, msg[:500])

    print("=== payment manager ===")
    time.sleep(3)
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
    print("  create-payment-manager", code, msg[:500])
    if data:
        pm = data.get("paymentManager") or data
        ids["PAYMENT_MANAGER_ARN"] = pm.get("paymentManagerArn") or data.get("paymentManagerArn")
        ids["PAYMENT_MANAGER_ID"] = pm.get("paymentManagerId") or data.get("paymentManagerId")

    print("=== registry retry ===")
    code, data, msg = aws_json(
        [
            "bedrock-agentcore-control",
            "create-registry",
            "--name",
            "credealrentroll",
            "--description",
            "CRE rent-roll skills",
            "--authorizer-type",
            "AWS_IAM",
        ]
    )
    print("  create-registry", code, msg[:400])
    if data:
        rg = data.get("registry") or data
        ids["REGISTRY_ID"] = rg.get("registryId") or data.get("registryId")

    print("=== api key credential provider ===")
    _, rt, _ = aws_json(["bedrock-agentcore-control", "get-agent-runtime", "--agent-runtime-id", "cre_deal_rentroll-Nh63z6GXmK"])
    openai_key = ((rt or {}).get("environmentVariables") or {}).get("OPENAI_API_KEY") or ""
    if openai_key:
        code, data, msg = aws_json(
            [
                "bedrock-agentcore-control",
                "create-api-key-credential-provider",
                "--name",
                "cre-deal-rentroll-openai",
                "--api-key",
                openai_key,
            ]
        )
        print("  create-api-key-credential-provider", code, msg[:400])
        if data:
            ids["API_KEY_PROVIDER_ARN"] = data.get("credentialProviderArn") or data.get("apiKeySecretArn")
            ids["API_KEY_PROVIDER_NAME"] = data.get("name") or "cre-deal-rentroll-openai"
        if not ids.get("API_KEY_PROVIDER_ARN"):
            _, listed, msg = aws_json(["bedrock-agentcore-control", "list-api-key-credential-providers"])
            print("  list-api-key-credential-providers", msg[:200])
            for item in (listed or {}).get("credentialProviders") or (listed or {}).get("apiKeyCredentialProviders") or []:
                if "openai" in (item.get("name") or "").lower() or "cre-deal" in (item.get("name") or ""):
                    ids["API_KEY_PROVIDER_ARN"] = item.get("credentialProviderArn") or item.get("arn")
                    ids["API_KEY_PROVIDER_NAME"] = item.get("name")

    print("=== harness ===")
    env = {"agentCoreRuntimeEnvironment": {"networkConfiguration": {"networkMode": "PUBLIC"}}}
    artifact = {
        "containerConfiguration": {
            "containerUri": "226721156429.dkr.ecr.us-east-1.amazonaws.com/cre-deal-rentroll-agentcore:latest"
        }
    }
    model = {"openAiModelConfig": {"modelId": "gpt-4o-mini"}}
    if ids.get("API_KEY_PROVIDER_ARN"):
        model["openAiModelConfig"]["apiKeyArn"] = ids["API_KEY_PROVIDER_ARN"]
    env_p = Path(tempfile.gettempdir()) / "harness-env.json"
    art_p = Path(tempfile.gettempdir()) / "harness-art.json"
    mod_p = Path(tempfile.gettempdir()) / "harness-model.json"
    env_p.write_text(json.dumps(env), encoding="utf-8")
    art_p.write_text(json.dumps(artifact), encoding="utf-8")
    mod_p.write_text(json.dumps(model), encoding="utf-8")
    h_args = [
        "bedrock-agentcore-control",
        "create-harness",
        "--harness-name",
        "cre_deal_rentroll",
        "--execution-role-arn",
        f"arn:aws:iam::{ACCOUNT}:role/CreDealRentrollAgentCoreRuntime",
        "--environment",
        f"file://{env_p}",
        "--environment-artifact",
        f"file://{art_p}",
    ]
    if ids.get("API_KEY_PROVIDER_ARN"):
        h_args += ["--model", f"file://{mod_p}"]
    code, data, msg = aws_json(h_args)
    print("  create-harness", code, msg[:600])
    if data:
        h = data.get("harness") or data
        ids["HARNESS_ID"] = h.get("harnessId") or data.get("harnessId")
        ids["HARNESS_ARN"] = h.get("harnessArn") or data.get("harnessArn")

    print("=== online evaluation ===")
    code, data, msg = aws_json(
        [
            "bedrock-agentcore-control",
            "create-online-evaluation-config",
            "--online-evaluation-config-name",
            "cre_deal_rentroll_online",
            "--rule",
            "samplingConfig={samplingPercentage=100.0}",
            "--data-source-config",
            "cloudWatchLogs={logGroupNames=[/aws/bedrock-agentcore/runtimes/cre_deal_rentroll-Nh63z6GXmK-DEFAULT],serviceNames=[cre_deal_rentroll]}",
            "--evaluators",
            "evaluatorId=Builtin.Faithfulness",
            "--evaluation-execution-role-arn",
            f"arn:aws:iam::{ACCOUNT}:role/CreDealRentrollEval",
            "--enable-on-create",
        ]
    )
    print("  create-online-evaluation-config", code, msg[:500])
    if data:
        ids["ONLINE_EVAL_ID"] = data.get("onlineEvaluationConfigId") or (data.get("onlineEvaluationConfig") or {}).get(
            "onlineEvaluationConfigId"
        )

    save(ids)
    print(json.dumps({k: v for k, v in ids.items() if "KEY" not in k}, indent=2)[:5000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
