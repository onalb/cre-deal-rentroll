"""score_gcr uses its handler in-process, and boto3 when the ARN is set."""

from __future__ import annotations

import json

from cre_rentroll.schemas import CanonicalRentRoll, EvalScores, UnitRow
from cre_rentroll.score_gcr_lambda import handler
from cre_rentroll.tools import score_gcr
from cre_rentroll.turn_ctx import reset_turn, set_eval_query


def _scores(**extra) -> EvalScores:
    payload = {
        "R": 5,
        "G": 5,
        "request_C": 5,
        "vault_calc_ok": True,
        "judge": "llm",
        "reason": "grounded",
    }
    payload.update(extra)
    return EvalScores(**payload)


def test_handler_calls_live_judge(monkeypatch):
    seen: dict = {}

    def fake_judge(query, answer, roll, traces):
        seen["query"] = query
        seen["answer"] = answer
        seen["roll"] = roll
        seen["n"] = len(traces)
        return _scores(G=4)

    monkeypatch.setattr("cre_rentroll.eval_service.live_judge", fake_judge)
    body = handler(
        {
            "query": "What is occupancy?",
            "draft": "Occupancy is 92%.",
            "roll": {
                "layout": "one_row",
                "property_name": "Hill",
                "units": [{"unit_id": "1", "in_place_rent": 1500, "vacant": False}],
            },
            "traces": [],
        },
        None,
    )
    assert seen["query"] == "What is occupancy?"
    assert seen["answer"] == "Occupancy is 92%."
    assert isinstance(seen["roll"], CanonicalRentRoll)
    assert seen["roll"].property_name == "Hill"
    assert body["scores"]["G"] == 4
    assert "username" not in body


def test_score_gcr_uses_handler_when_arn_is_unset(monkeypatch):
    monkeypatch.delenv("SCORE_GCR_LAMBDA_ARN", raising=False)
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *args, **kwargs: _scores(),
    )
    from cre_rentroll import score_gcr_lambda

    seen: list[dict] = []
    real = score_gcr_lambda.handler

    def spy(event, context):
        seen.append(event)
        return real(event, context)

    monkeypatch.setattr(score_gcr_lambda, "handler", spy)
    reset_turn("secret-user")
    set_eval_query("What is occupancy?")
    out = json.loads(score_gcr("Occupancy is 92%."))
    assert out["decision"] == "ok"
    assert out["G"] == 5
    assert len(seen) == 1
    event = seen[0]
    assert event["draft"] == "Occupancy is 92%."
    assert event["query"] == "What is occupancy?"
    assert set(event) == {"query", "draft", "roll", "traces"}
    assert event["roll"] is roll


def test_score_gcr_invokes_lambda_when_arn_is_set(monkeypatch):
    arn = "arn:aws:lambda:us-east-1:123:function:score-gcr"
    monkeypatch.setenv("SCORE_GCR_LAMBDA_ARN", arn)
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)

    def boom(*args, **kwargs):
        raise AssertionError("live_judge runs in the Lambda when the ARN is set")

    monkeypatch.setattr("cre_rentroll.eval_service.live_judge", boom)
    captured: dict = {}

    class _Payload:
        def read(self):
            return json.dumps({"scores": _scores().model_dump()}).encode("utf-8")

    class _Client:
        def invoke(self, **kwargs):
            captured.update(kwargs)
            return {"StatusCode": 200, "Payload": _Payload()}

    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: _Client())
    reset_turn("secret-user")
    set_eval_query("What is occupancy?")
    out = json.loads(score_gcr("Occupancy is 92%."))
    assert out["decision"] == "ok"
    assert out["judge"] == "llm"
    assert captured["FunctionName"] == arn
    sent = json.loads(captured["Payload"])
    assert sent["draft"] == "Occupancy is 92%."
    assert sent["query"] == "What is occupancy?"
    assert sent["roll"]["property_name"] == "Hill"
    assert "username" not in sent
    assert "force_fail" not in sent
