"""score_gcr Lambda. The deal app invokes this. It is not a gateway tool.

Entry point: ``cre_rentroll.score_gcr_lambda.handler``.

Event::

    {"query": "...", "draft": "...", "roll": {...} | null, "traces": [...]}

The handler calls ``live_judge`` and returns ``{"scores": {...}}``.
Username, as-of, search-fail, and the question stay in the deal
process. This module does not read them and does not write the checklist.

``SCORE_GCR_LAMBDA_ARN`` unset: ``judge`` calls ``handler`` in-process.
Set: ``judge`` calls ``boto3`` ``lambda.invoke``. Local pytest leaves it unset.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cre_rentroll.schemas import CanonicalRentRoll, EvalScores


def score_gcr_lambda_arn() -> str:
    return os.environ.get("SCORE_GCR_LAMBDA_ARN", "").strip()


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Run the live judge on one draft. ``context`` is unused for local calls."""
    del context
    if not isinstance(event, dict):
        raise TypeError("event must be an object")
    from cre_rentroll.eval_service import live_judge

    query = str(event.get("query") or "")
    draft = str(event.get("draft") or "")
    traces = event.get("traces") or []
    if not isinstance(traces, list):
        raise TypeError("traces must be a list")
    roll = _roll(event.get("roll"))
    scores = live_judge(query, draft, roll, traces)
    return {"scores": scores.model_dump()}


def judge(query: str, draft: str, roll: CanonicalRentRoll | None, traces: list[Any]) -> EvalScores:
    """In-process handler, or a Lambda invoke when ``SCORE_GCR_LAMBDA_ARN`` is set."""
    arn = score_gcr_lambda_arn()
    if not arn:
        body = handler(
            {"query": query, "draft": draft, "roll": roll, "traces": list(traces or [])},
            None,
        )
    else:
        body = _invoke_lambda(
            arn,
            {
                "query": query,
                "draft": draft,
                "roll": None if roll is None else roll.model_dump(mode="json"),
                "traces": json.loads(json.dumps(list(traces or []), default=str)),
            },
        )
    if not isinstance(body, dict) or not isinstance(body.get("scores"), dict):
        raise RuntimeError("score_gcr handler returned an unexpected payload")
    return EvalScores.model_validate(body["scores"])


def _roll(raw: Any) -> CanonicalRentRoll | None:
    if raw is None:
        return None
    if isinstance(raw, CanonicalRentRoll):
        return raw
    if isinstance(raw, dict):
        return CanonicalRentRoll.model_validate(raw)
    raise TypeError("roll must be an object")


def _invoke_lambda(arn: str, event: dict[str, Any]) -> dict[str, Any]:
    import boto3

    client = boto3.client("lambda")
    response = client.invoke(
        FunctionName=arn,
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode("utf-8"),
    )
    raw = response["Payload"].read()
    text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw or "")
    try:
        body = json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("score_gcr Lambda returned non-JSON") from exc
    if response.get("FunctionError"):
        message = ""
        if isinstance(body, dict):
            message = str(body.get("errorMessage") or body.get("errorType") or "")
        raise RuntimeError(message or "score_gcr Lambda invoke failed")
    if not isinstance(body, dict):
        raise RuntimeError("score_gcr Lambda returned an unexpected payload")
    return body
