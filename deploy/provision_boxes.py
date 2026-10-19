"""Create remaining AgentCore platform boxes. No secrets written to disk."""
from __future__ import annotations

import json
import os
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
AWS = os.environ.get("AWS_CLI", r"C:\Program Files\Amazon\AWSCLIV2\aws.exe")
PY = sys.executable
os.environ.setdefault("AWS_PROFILE", "default")
os.environ.setdefault("AWS_DEFAULT_REGION", REGION)
os.environ.setdefault("AWS_PAGER", "")


def run(args: list[str], check: bool = False) -> tuple[int, str, str]:
    p = subprocess.run(
        [AWS, *args, "--region", REGION],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return p.returncode, p.stdout, p.stderr


def aws_json(args: list[str]) -> tuple[int, dict | None, str]:
    code, out, err = run(args + ["--output", "json"])
    data = None
    if out.strip():
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = None
    msg = (err or out or "").replace("\n", " ")[:700]
    return code, data, msg


def load_ids() -> dict:
    if OUT.exists():
        try:
            return json.loads(OUT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_ids(ids: dict) -> None:
    clean = {k: v for k, v in ids.items() if v and "KEY" not in k.upper() and "SECRET" not in k.upper()}
    OUT.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    print("wrote", OUT)


def main() -> int:
    ids = load_ids()
    ids.update(
        {
            "WORKLOAD_NAME": "cre_deal_rentroll-Nh63z6GXmK",
            "RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:226721156429:runtime/cre_deal_rentroll-Nh63z6GXmK",
            "EVALUATOR_ID": "Builtin.Faithfulness",
            "MEMORY_ID": ids.get("MEMORY_ID") or "CreDealRentrollMem-l47sZRGE6Y",
            "MEMORY_ARN": ids.get("MEMORY_ARN")
            or "arn:aws:bedrock-agentcore:us-east-1:226721156429:memory/CreDealRentrollMem-l47sZRGE6Y",
            "CODE_INTERPRETER_ID": ids.get("CODE_INTERPRETER_ID") or "cre_deal_rentroll_ci-gn1EOecInj",
            "BROWSER_ID": ids.get("BROWSER_ID") or "cre_deal_rentroll_browser-Ic5Tw7nTd6",
            "LAMBDA_ARN": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:cre-deal-rentroll-gateway",
        }
    )

    print("=== IAM policies ===")
    for role, pname, doc in [
        ("CreDealRentrollAgentCoreRuntime", "agentcore-exec", DEPLOY / "agentcore-exec-policy.json"),
        ("CreDealRentrollGateway", "gateway", DEPLOY / "gateway-role-policy.json"),
        ("CreDealRentrollLambda", "lambda-ddb", DEPLOY / "lambda-role-policy.json"),
        ("CreDealRentrollEval", "eval", DEPLOY / "eval-role-policy.json"),
    ]:
        code, _, msg = aws_json(
            ["iam", "put-role-policy", "--role-name", role, "--policy-name", pname, "--policy-document", f"file://{doc}"]
        )
        print(f"  {role}/{pname} exit={code} {msg[:120]}")

    print("=== policy engine ===")
    code, data, msg = aws_json(
        [
            "bedrock-agentcore-control",
            "create-policy-engine",
            "--name",
            "cre_deal_rentroll_policy",
            "--description",
            "Allow CRE MCP tools on Gateway",
        ]
    )
    print("  create-policy-engine", code, msg[:250])
    if data:
        pe = data.get("policyEngine") or data
        ids["POLICY_ENGINE_ID"] = pe.get("policyEngineId") or data.get("policyEngineId")
        ids["POLICY_ENGINE_ARN"] = pe.get("policyEngineArn") or data.get("policyEngineArn")
    if not ids.get("POLICY_ENGINE_ID"):
        code, listed, msg = aws_json(["bedrock-agentcore-control", "list-policy-engines"])
        print("  list-policy-engines", code, msg[:200])
        for item in (listed or {}).get("policyEngines") or []:
            name = item.get("name") or ""
            if "cre_deal" in name or "cre_deal" in (item.get("policyEngineId") or ""):
                ids["POLICY_ENGINE_ID"] = item.get("policyEngineId")
                ids["POLICY_ENGINE_ARN"] = item.get("policyEngineArn") or item.get("arn")

    if ids.get("POLICY_ENGINE_ID"):
        cedar = {"cedar": {"statement": "permit (principal, action, resource);"}}
        cedar_path = Path(tempfile.gettempdir()) / "cre-cedar.json"
        cedar_path.write_text(json.dumps(cedar), encoding="utf-8")
        code, data, msg = aws_json(
            [
                "bedrock-agentcore-control",
                "create-policy",
                "--name",
                "allow_cre_tools",
                "--policy-engine-id",
                ids["POLICY_ENGINE_ID"],
                "--definition",
                f"file://{cedar_path}",
                "--enforcement-mode",
                "LOG_ONLY",
                "--validation-mode",
                "IGNORE_ALL_FINDINGS",
            ]
        )
        print("  create-policy", code, msg[:300])
        if data:
            pol = data.get("policy") or data
            ids["POLICY_ID"] = pol.get("policyId") or data.get("policyId")

    print("=== payment manager ===")
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
    print("  create-payment-manager", code, msg[:300])
    if data:
        pm = data.get("paymentManager") or data
        ids["PAYMENT_MANAGER_ARN"] = pm.get("paymentManagerArn") or data.get("paymentManagerArn")
        ids["PAYMENT_MANAGER_ID"] = pm.get("paymentManagerId") or data.get("paymentManagerId")

    print("=== registry ===")
    code, data, msg = aws_json(
        [
            "bedrock-agentcore-control",
            "create-registry",
            "--name",
            "cre_deal_rentroll",
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
        ids["REGISTRY_ARN"] = rg.get("registryArn") or data.get("registryArn") or rg.get("arn")
    if not ids.get("REGISTRY_ID"):
        code, listed, msg = aws_json(["bedrock-agentcore-control", "list-registries"])
        print("  list-registries", code, msg[:250])
        for item in (listed or {}).get("registries") or (listed or {}).get("items") or []:
            if (item.get("name") or "") == "cre_deal_rentroll":
                ids["REGISTRY_ID"] = item.get("registryId") or item.get("id")
                ids["REGISTRY_ARN"] = item.get("arn") or item.get("registryArn")

    if ids.get("REGISTRY_ID"):
        for sk in ("rent-roll-abstract", "citation-policy", "underwriting"):
            md = (ROOT / "skills" / sk / "SKILL.md").read_text(encoding="utf-8")
            desc = {"agentSkills": {"skillMd": {"inlineContent": md}}}
            df = Path(tempfile.gettempdir()) / f"skill-{sk}.json"
            df.write_text(json.dumps(desc), encoding="utf-8")
            code, data, msg = aws_json(
                [
                    "bedrock-agentcore-control",
                    "create-registry-record",
                    "--registry-id",
                    ids["REGISTRY_ID"],
                    "--name",
                    sk,
                    "--descriptor-type",
                    "AGENT_SKILLS",
                    "--descriptors",
                    f"file://{df}",
                ]
            )
            print(f"  registry-record {sk}", code, msg[:250])

    print("=== lambda zip ===")
    lam_dir = Path(tempfile.gettempdir()) / "cre-gw-lambda"
    if lam_dir.exists():
        import shutil

        shutil.rmtree(lam_dir, ignore_errors=True)
    lam_dir.mkdir(parents=True)
    (lam_dir / "handler.py").write_bytes((DEPLOY / "lambda_gateway" / "handler.py").read_bytes())
    import shutil

    shutil.copytree(ROOT / "api" / "cre_rentroll", lam_dir / "cre_rentroll")
    subprocess.run(
        [
            PY,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-q",
            "pydantic",
            "httpx",
            "ddgs",
            "openpyxl",
            "python-dotenv",
            "boto3",
            "-t",
            str(lam_dir),
        ],
        check=False,
    )
    zip_path = Path(tempfile.gettempdir()) / "cre-gw-lambda.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in lam_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(lam_dir).as_posix())
    print("  zip bytes", zip_path.stat().st_size)

    code, data, msg = aws_json(["lambda", "get-function", "--function-name", "cre-deal-rentroll-gateway"])
    if code != 0:
        print("  waiting for lambda role...")
        time.sleep(8)
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
                "Variables={DDB_TABLE=ml_interview,AWS_REGION=us-east-1}",
            ]
        )
        print("  create-function", code, msg[:300])
    else:
        code, data, msg = aws_json(
            [
                "lambda",
                "update-function-code",
                "--function-name",
                "cre-deal-rentroll-gateway",
                "--zip-file",
                f"fileb://{zip_path}",
            ]
        )
        print("  update-function-code", code, msg[:200])
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

    print("=== gateway ===")
    proto = {
        "mcp": {
            "supportedVersions": ["2025-03-26"],
            "instructions": "CRE rent-roll MCP tools",
            "disableMcpListToolsPagination": True,
        }
    }
    proto_path = Path(tempfile.gettempdir()) / "gw-proto.json"
    proto_path.write_text(json.dumps(proto), encoding="utf-8")
    gw_args = [
        "bedrock-agentcore-control",
        "create-gateway",
        "--name",
        "cre-deal-rentroll",
        "--role-arn",
        f"arn:aws:iam::{ACCOUNT}:role/CreDealRentrollGateway",
        "--protocol-type",
        "MCP",
        "--authorizer-type",
        "AWS_IAM",
        "--protocol-configuration",
        f"file://{proto_path}",
    ]
    if ids.get("POLICY_ENGINE_ARN"):
        gw_args += ["--policy-engine-configuration", f"arn={ids['POLICY_ENGINE_ARN']},mode=LOG_ONLY"]
    code, data, msg = aws_json(gw_args)
    print("  create-gateway", code, msg[:400])
    if data:
        g = data.get("gateway") or data
        ids["GATEWAY_ID"] = g.get("gatewayId") or data.get("gatewayId")
        ids["GATEWAY_ARN"] = g.get("gatewayArn") or data.get("gatewayArn")
        ids["GATEWAY_URL"] = g.get("gatewayUrl") or data.get("gatewayUrl")
    if not ids.get("GATEWAY_ID"):
        code, listed, msg = aws_json(["bedrock-agentcore-control", "list-gateways"])
        print("  list-gateways", code, msg[:250])
        for item in (listed or {}).get("items") or []:
            if item.get("name") == "cre-deal-rentroll":
                ids["GATEWAY_ID"] = item.get("gatewayId")
                ids["GATEWAY_ARN"] = item.get("gatewayArn")
                ids["GATEWAY_URL"] = item.get("gatewayUrl")

    if ids.get("GATEWAY_ID"):
        tools = json.loads((DEPLOY / "gateway-tools.json").read_text(encoding="utf-8"))
        target = {"mcp": {"lambda": {"lambdaArn": ids["LAMBDA_ARN"], "toolSchema": {"inlinePayload": tools}}}}
        tc = Path(tempfile.gettempdir()) / "gw-target.json"
        tc.write_text(json.dumps(target), encoding="utf-8")
        cred = Path(tempfile.gettempdir()) / "gw-cred.json"
        cred.write_text(json.dumps([{"credentialProviderType": "GATEWAY_IAM_ROLE"}]), encoding="utf-8")
        code, data, msg = aws_json(
            [
                "bedrock-agentcore-control",
                "create-gateway-target",
                "--gateway-identifier",
                ids["GATEWAY_ID"],
                "--name",
                "cre-tools",
                "--target-configuration",
                f"file://{tc}",
                "--credential-provider-configurations",
                f"file://{cred}",
            ]
        )
        print("  create-gateway-target", code, msg[:400])
        if ids.get("GATEWAY_ID") and not ids.get("GATEWAY_URL"):
            code, data, msg = aws_json(
                ["bedrock-agentcore-control", "get-gateway", "--gateway-identifier", ids["GATEWAY_ID"]]
            )
            g = (data or {}).get("gateway") or (data or {})
            ids["GATEWAY_URL"] = g.get("gatewayUrl")
            ids["GATEWAY_ARN"] = ids.get("GATEWAY_ARN") or g.get("gatewayArn")

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
            "cloudWatchLogs={logGroupNames=[/aws/bedrock-agentcore/runtimes/cre_deal_rentroll-Nh63z6GXmK-DEFAULT]}",
            "--evaluators",
            "evaluatorId=Builtin.Faithfulness",
            "--evaluation-execution-role-arn",
            f"arn:aws:iam::{ACCOUNT}:role/CreDealRentrollEval",
            "--enable-on-create",
        ]
    )
    print("  create-online-evaluation-config", code, msg[:400])
    if data:
        ids["ONLINE_EVAL_ID"] = data.get("onlineEvaluationConfigId") or (data.get("onlineEvaluationConfig") or {}).get(
            "onlineEvaluationConfigId"
        )

    print("=== openai secret for harness ===")
    code, rt, msg = aws_json(
        ["bedrock-agentcore-control", "get-agent-runtime", "--agent-runtime-id", "cre_deal_rentroll-Nh63z6GXmK"]
    )
    openai_key = ((rt or {}).get("environmentVariables") or {}).get("OPENAI_API_KEY") or ""
    openai_arn = None
    if openai_key:
        code, data, msg = aws_json(["secretsmanager", "describe-secret", "--secret-id", "cre-deal-rentroll-openai"])
        if code != 0:
            code, data, msg = aws_json(
                ["secretsmanager", "create-secret", "--name", "cre-deal-rentroll-openai", "--secret-string", openai_key]
            )
            print("  create-secret", code, (msg[:80] if code else "ok"))
            openai_arn = (data or {}).get("ARN")
        else:
            aws_json(["secretsmanager", "put-secret-value", "--secret-id", "cre-deal-rentroll-openai", "--secret-string", openai_key])
            openai_arn = (data or {}).get("ARN") or f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:cre-deal-rentroll-openai"
        ids["OPENAI_SECRET_ARN"] = openai_arn

    print("=== harness ===")
    env = {"agentCoreRuntimeEnvironment": {"networkConfiguration": {"networkMode": "PUBLIC"}}}
    artifact = {
        "containerConfiguration": {
            "containerUri": "226721156429.dkr.ecr.us-east-1.amazonaws.com/cre-deal-rentroll-agentcore:latest"
        }
    }
    model = {"openAiModelConfig": {"modelId": "gpt-4o-mini"}}
    if openai_arn:
        model["openAiModelConfig"]["apiKeyArn"] = openai_arn
    env_p = Path(tempfile.gettempdir()) / "harness-env.json"
    art_p = Path(tempfile.gettempdir()) / "harness-art.json"
    mod_p = Path(tempfile.gettempdir()) / "harness-model.json"
    env_p.write_text(json.dumps(env), encoding="utf-8")
    art_p.write_text(json.dumps(artifact), encoding="utf-8")
    mod_p.write_text(json.dumps(model), encoding="utf-8")
    code, data, msg = aws_json(
        [
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
            "--model",
            f"file://{mod_p}",
        ]
    )
    print("  create-harness", code, msg[:500])
    if data:
        h = data.get("harness") or data
        ids["HARNESS_ID"] = h.get("harnessId") or data.get("harnessId")
        ids["HARNESS_ARN"] = h.get("harnessArn") or data.get("harnessArn")
    if not ids.get("HARNESS_ID"):
        code, listed, msg = aws_json(["bedrock-agentcore-control", "list-harnesses"])
        print("  list-harnesses", code, msg[:250])
        for item in (listed or {}).get("harnesses") or []:
            if "cre_deal" in (item.get("harnessName") or item.get("name") or ""):
                ids["HARNESS_ID"] = item.get("harnessId")
                ids["HARNESS_ARN"] = item.get("harnessArn") or item.get("arn")

    save_ids(ids)
    print(json.dumps({k: v for k, v in ids.items() if "KEY" not in k}, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
