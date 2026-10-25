"""Start AgentCore batch evaluation over Runtime CloudWatch traces.

Live G/C/R is still score_gcr. This is the AWS Evaluations box: score many
sessions from logs (Faithfulness / Helpfulness / GoalSuccessRate).

  py -3 deploy/start_batch_evaluation.py
  py -3 deploy/start_batch_evaluation.py --wait
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

REGION = "us-east-1"
RUNTIME = "cre_deal_rentroll-Nh63z6GXmK"
LOG_GROUP = f"/aws/bedrock-agentcore/runtimes/{RUNTIME}-DEFAULT"
# OTEL resource service.name is the Runtime *name*.DEFAULT, not the runtime id.
SERVICE = "cre_deal_rentroll.DEFAULT"
DEFAULT_EVALUATORS = (
    "Builtin.Faithfulness",
    "Builtin.Helpfulness",
    "Builtin.GoalSuccessRate",
)


def batch_request(
    *,
    lookback_days: int = 7,
    evaluators: tuple[str, ...] = DEFAULT_EVALUATORS,
    name: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    job = name or f"cre_batch_{stamp}"
    if not job[0].isalpha():
        job = "cre_" + job
    job = job.replace("-", "_")[:48]
    return {
        "batchEvaluationName": job,
        "evaluators": [{"evaluatorId": eid} for eid in evaluators],
        "dataSourceConfig": {
            "cloudWatchLogs": {
                "serviceNames": [SERVICE],
                "logGroupNames": [LOG_GROUP],
                "filterConfig": {
                    "timeRange": {
                        "startTime": (now - timedelta(days=lookback_days)).isoformat(),
                        "endTime": now.isoformat(),
                    }
                },
            }
        },
        "clientToken": str(uuid.uuid4()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    import boto3

    body = batch_request(lookback_days=args.lookback_days, name=args.name or None)
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    resp = client.start_batch_evaluation(**body)
    print(json.dumps(resp, default=str, indent=2))
    eid = resp.get("batchEvaluationId")
    if not args.wait or not eid:
        return 0
    while True:
        result = client.get_batch_evaluation(batchEvaluationId=eid)
        status = result.get("status")
        print("status", status)
        if status in {
            "COMPLETED",
            "COMPLETED_WITH_ERRORS",
            "FAILED",
            "STOPPED",
        }:
            print(json.dumps(result, default=str, indent=2))
            return 0 if status.startswith("COMPLETED") else 1
        time.sleep(20)


if __name__ == "__main__":
    raise SystemExit(main())
