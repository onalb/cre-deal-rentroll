import json

import httpx
import pytest

from cre_rentroll.agents import BOOK, _run_openai_tools_loop
from cre_rentroll.harness import run_task
from cre_rentroll.mcp_runtime import VAULT_MCP, mcp_server_for
from cre_rentroll.predict_client import (
    build_forecast_question,
    forecast,
    reset_predict_state,
)
from cre_rentroll.run_events import bind, reset
from cre_rentroll.schemas import CanonicalRentRoll, EvalScores, UnitRow
from cre_rentroll.tools import TOOL_IMPLS, ask_prediction_agent
from cre_rentroll.turn_ctx import reset_turn


def test_prediction_tool_is_on_the_book_menu():
    assert "ask_prediction_agent" in BOOK.tool_names
    assert "ask_vacancy" not in BOOK.tool_names
    assert TOOL_IMPLS["ask_prediction_agent"] is ask_prediction_agent
    assert mcp_server_for("ask_prediction_agent") == VAULT_MCP


def test_ask_prediction_agent_calls_http(monkeypatch):
    monkeypatch.setattr("cre_rentroll.tools.turn_ctx.username", lambda: "tester")
    recorded = {}
    monkeypatch.setattr(
        "cre_rentroll.tools.turn_ctx.record",
        lambda name, args, result: recorded.update(name=name, args=args, result=result),
    )
    monkeypatch.setattr(
        "cre_rentroll.predict_client.forecast",
        lambda user, question, horizon_months=12, vault=None: {
            "ok": True,
            "source": "vacancy-prediction-agent",
            "answer": "12 expirations",
            "horizon_months": horizon_months,
        },
    )
    payload = json.loads(ask_prediction_agent(12, "forecast occupancy"))
    assert recorded["name"] == "ask_prediction_agent"
    assert payload["answer"] == "12 expirations"
    assert payload["source"] == "vacancy-prediction-agent"
    assert "train" not in payload


class FakeResponse:
    def __init__(self, payload: dict | None = None, status_code: int = 200, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://127.0.0.1:8000/mcp")
            raise httpx.HTTPStatusError(
                "error",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self) -> dict:
        return self._payload


def _ok_call_result(text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"content": [{"type": "text", "text": text}]},
    }


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        raise AssertionError("forecast must not stream SSE /api/chat")

    def post(self, url, **kwargs):
        body = kwargs.get("json") or {}
        self.calls.append({"method": "POST", "url": url, "json": body})
        if "/api/upload" in url or "/api/chat" in url:
            raise AssertionError("forecast must not POST /api/chat or /api/upload")
        method = body.get("method")
        if method == "initialize":
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}})
        if method == "notifications/initialized":
            return FakeResponse({})
        if method == "tools/call":
            assert body["params"]["name"] == "ask_vacancy"
            return FakeResponse(_ok_call_result("building not trained"))
        raise AssertionError(f"unexpected MCP method {method}")


def test_forecast_posts_mcp_ask_vacancy_not_chat(monkeypatch):
    reset_predict_state()
    client = FakeClient()

    def factory(*args, **kwargs):
        return client

    monkeypatch.setattr(httpx, "Client", factory)
    result = forecast(
        "tester",
        "what occupancy do you predict?",
        horizon_months=12,
        vault={
            "property_name": "La Cordoba",
            "as_of": "2024-06-01",
            "unit_count": 164,
            "occupancy_pct": 0.9207,
            "vacant_count": 13,
            "floor_plans": ["1x1 B", "2x2 A"],
        },
    )
    assert result["ok"] is True
    assert result["answer"] == "building not trained"
    assert "train" not in result
    assert client.calls
    assert all(c["url"].endswith("/mcp") for c in client.calls)
    assert all("/api/chat" not in c["url"] for c in client.calls)
    assert all("/api/upload" not in c["url"] for c in client.calls)
    call = next(c for c in client.calls if c["json"].get("method") == "tools/call")
    payload = call["json"]["params"]
    assert payload["name"] == "ask_vacancy"
    args = payload["arguments"]
    question = args["question"]
    assert "La Cordoba" not in question
    assert "1x1 B" not in question
    assert "do not parse" in question.lower()
    assert "what occupancy do you predict?" in question.lower()
    assert args["session_id"]
    assert "file_id" not in question
    assert "Train this rent roll" not in question


def test_forecast_reuses_session_id(monkeypatch):
    reset_predict_state()
    client = FakeClient()
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)
    forecast("tester", "first")
    forecast("tester", "second")
    calls = [c for c in client.calls if c["json"].get("method") == "tools/call"]
    ids = [c["json"]["params"]["arguments"]["session_id"] for c in calls]
    assert len(ids) == 2
    assert ids[0] == ids[1]


def test_forecast_followup_sends_raw_user_text(monkeypatch):
    reset_predict_state()
    client = FakeClient()
    replies = [
        "Which floor plan? • Plan A\n• Plan B",
        "Plan A is vacant with probability 0.12.",
    ]

    class SequenceClient(FakeClient):
        def post(self, url, **kwargs):
            body = kwargs.get("json") or {}
            self.calls.append({"method": "POST", "url": url, "json": body})
            method = body.get("method")
            if method == "initialize":
                return FakeResponse(
                    {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}
                )
            if method == "notifications/initialized":
                return FakeResponse({})
            if method == "tools/call":
                text = replies.pop(0)
                return FakeResponse(_ok_call_result(text))
            raise AssertionError(f"unexpected MCP method {method}")

    seq = SequenceClient()
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: seq)
    first = forecast(
        "tester",
        "What occupancy do you predict?",
        vault={"property_name": "Fixture Towers", "as_of": "2024-01-01"},
    )
    assert "Which floor plan?" in first["answer"]
    second = forecast("tester", "Plan A")
    assert "0.12" in second["answer"]
    calls = [c for c in seq.calls if c["json"].get("method") == "tools/call"]
    questions = [c["json"]["params"]["arguments"]["question"] for c in calls]
    ids = [c["json"]["params"]["arguments"]["session_id"] for c in calls]
    assert "Fixture Towers" not in questions[0]
    assert "What occupancy do you predict?" in questions[0]
    assert "do not parse" in questions[0].lower()
    assert questions[1] == "Plan A"
    assert "Score occupancy" not in questions[1]
    assert ids[0] == ids[1]


def test_ask_prediction_agent_maps_mcp_failure(monkeypatch):
    monkeypatch.setattr("cre_rentroll.tools.turn_ctx.username", lambda: "tester")
    monkeypatch.setattr("cre_rentroll.tools.turn_ctx.record", lambda *a, **k: None)

    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("cre_rentroll.predict_client.forecast", boom)
    payload = json.loads(ask_prediction_agent(12, "forecast occupancy"))
    assert payload["ok"] is False
    assert "connection refused" in payload["error"]
    assert payload["source"] == "vacancy-prediction-agent"


def test_forecast_raises_on_http_error(monkeypatch):
    reset_predict_state()

    class FailClient(FakeClient):
        def post(self, url, **kwargs):
            self.calls.append({"method": "POST", "url": url, "json": kwargs.get("json")})
            return FakeResponse(status_code=500)

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: FailClient())
    with pytest.raises(RuntimeError):
        forecast("tester", "what occupancy do you predict?")


def test_forecast_occupancy_only_does_not_inject_vault_name():
    text = build_forecast_question(
        "What occupancy do you predict?",
        vault={
            "property_name": "La Cordoba",
            "as_of": "2024-06-01",
            "unit_count": 164,
            "occupancy_pct": 0.9207,
            "vacant_count": 13,
            "floor_plans": ["1x1 B", "2x2 A"],
        },
    )
    assert "La Cordoba" not in text
    assert "1x1 B" not in text
    assert "2x2 A" not in text
    assert "do not parse" in text.lower()
    assert "What occupancy do you predict?" in text
    assert "unit_count=164" not in text


def test_forecast_question_keeps_user_named_property():
    text = build_forecast_question(
        "What occupancy do you predict for La Cordoba?",
        vault={
            "property_name": "La Cordoba",
            "as_of": "2024-06-01",
            "unit_count": 164,
            "occupancy_pct": 0.9207,
            "vacant_count": 13,
        },
    )
    assert "La Cordoba" in text
    assert "do not parse" in text.lower()
    assert "What occupancy do you predict for La Cordoba?" in text
    assert "What occupancy do you predict for La Cordoba as of" not in text
    assert "Score occupancy for La Cordoba as of 2024-06-01" in text
    assert "unit_count=164" in text


def test_full_forecast_question_skips_vault_name_and_parse():
    text = build_forecast_question(
        "Over the next 12 months, how many units expire or are MTM, what income is at risk, and what occupancy do you predict?",
        vault={"property_name": "La Cordoba", "as_of": "2024-06-01", "unit_count": 10, "occupancy_pct": 0.9, "vacant_count": 1},
    )
    assert "La Cordoba" not in text
    assert "do not parse" in text.lower()
    assert "Do not request a rent roll file" in text
    assert "expire" in text.lower()


def test_ask_prediction_agent_prompt_includes_vault_name(monkeypatch):
    reset_turn("tester")
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="La Cordoba",
        as_of="2024-06-01",
        units=[
            UnitRow(unit_id="1", unit_type="1x1 B", in_place_rent=1000, vacant=False),
            UnitRow(unit_id="2", unit_type="2x2 A", in_place_rent=0, vacant=True),
        ],
    )
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.tools.turn_ctx.record", lambda *a, **k: None)
    captured = {}

    def fake_forecast(user, question, horizon_months=12, vault=None):
        captured.update(question=question, vault=vault, user=user)
        return {"ok": True, "source": "vacancy-prediction-agent", "answer": "which plan?"}

    monkeypatch.setattr("cre_rentroll.predict_client.forecast", fake_forecast)
    payload = json.loads(ask_prediction_agent(12, "What occupancy do you predict?"))
    assert captured["vault"]["property_name"] == "La Cordoba"
    assert captured["question"] == "What occupancy do you predict?"
    assert payload["answer"] == "which plan?"


def _predict_roll() -> CanonicalRentRoll:
    return CanonicalRentRoll(
        layout="one_row",
        property_name="Fixture Towers",
        as_of="2024-06-01",
        units=[
            UnitRow(unit_id="1", unit_type="Plan A", in_place_rent=1000, vacant=False),
            UnitRow(unit_id="2", unit_type="Plan B", in_place_rent=0, vacant=True),
        ],
    )


def _stub_book_run(monkeypatch, roll: CanonicalRentRoll) -> None:
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(R=4, G=4, request_C=4, vault_calc_ok=True, judge="llm"),
    )


def test_untrained_mcp_reply_is_the_book_answer(monkeypatch):
    reset_predict_state()
    roll = _predict_roll()
    _stub_book_run(monkeypatch, roll)
    clarification = (
        "That building is not trained. Trained properties: Alpha Court, Beta Place."
    )

    def fake_forecast(user, question, horizon_months=12, vault=None):
        return {
            "ok": True,
            "source": "vacancy-prediction-agent",
            "answer": clarification,
            "horizon_months": horizon_months,
        }

    monkeypatch.setattr("cre_rentroll.predict_client.forecast", fake_forecast)

    def fake_agent(*_a, **_k):
        ask_prediction_agent(12, "What occupancy do you predict?")
        return "Occupancy is 50 percent from the vault roll."

    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    token = bind(lambda *_a, **_k: None)
    try:
        outcome = run_task(
            "tester",
            "What occupancy do you predict?",
            skip_scope=True,
            chip_id="predict_occupancy_only",
        )
    finally:
        reset(token)
    assert clarification in (outcome.text or "")
    assert "50 percent" not in (outcome.text or "")


def test_ambiguous_plan_mcp_is_quoted_and_followup_calls_mcp(monkeypatch):
    reset_predict_state()
    roll = _predict_roll()
    _stub_book_run(monkeypatch, roll)
    questions: list[str] = []
    replies = [
        "Which floor plan do you want?\n• Plan A\n• Plan B",
        "Plan A is vacant with probability 0.11 as of 2024-06-01.",
    ]

    def fake_forecast(user, question, horizon_months=12, vault=None):
        questions.append(question)
        return {
            "ok": True,
            "source": "vacancy-prediction-agent",
            "answer": replies[len(questions) - 1],
            "horizon_months": horizon_months,
        }

    monkeypatch.setattr("cre_rentroll.predict_client.forecast", fake_forecast)

    def first_agent(*_a, **_k):
        ask_prediction_agent(12, "What occupancy do you predict?")
        return "Occupancy is 50 percent."

    monkeypatch.setattr("cre_rentroll.harness.run_agent", first_agent)
    token = bind(lambda *_a, **_k: None)
    try:
        first = run_task(
            "tester",
            "What occupancy do you predict?",
            skip_scope=True,
            chip_id="predict",
        )
        assert "Which floor plan" in (first.text or "")
        assert "Plan A" in (first.text or "")
        assert "50 percent" not in (first.text or "")

        def follow_agent(*_a, **_k):
            payload = json.loads(ask_prediction_agent(12, "Plan A"))
            return payload["answer"]

        monkeypatch.setattr("cre_rentroll.harness.run_agent", follow_agent)
        second = run_task("tester", "Plan A", skip_scope=True, chip_id="predict")
    finally:
        reset(token)
    assert len(questions) == 2
    assert questions[1] == "Plan A"
    assert "0.11" in (second.text or "")


def test_same_turn_second_ask_does_not_post_mcp(monkeypatch):
    reset_predict_state()
    reset_turn("tester")
    client = FakeClient()
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: None)
    first = json.loads(ask_prediction_agent(12, "What occupancy do you predict?"))
    second = json.loads(ask_prediction_agent(12, "1x1 B"))
    third = json.loads(ask_prediction_agent(12, "2x2 G in La Cordoba"))
    calls = [c for c in client.calls if c["json"].get("method") == "tools/call"]
    assert len(calls) == 1
    assert first["answer"] == "building not trained"
    assert second["answer"] == "building not trained"
    assert second.get("same_turn_stop") is True
    assert third.get("same_turn_stop") is True
    assert third["answer"] == "building not trained"


def test_openai_loop_stops_after_predictor_clarify(monkeypatch):
    reset_predict_state()
    reset_turn("tester")
    clarify = "Which floor plan should I score?\n• Plan A\n• Plan B"

    class Fn:
        def __init__(self, name, arguments="{}"):
            self.name = name
            self.arguments = arguments

    class TC:
        def __init__(self, name="ask_prediction_agent", arguments='{"question":"What occupancy do you predict?"}'):
            self.id = "1"
            self.function = Fn(name, arguments)

        def model_dump(self):
            return {
                "id": self.id,
                "type": "function",
                "function": {"name": self.function.name, "arguments": self.function.arguments},
            }

    class Msg:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class Resp:
        def __init__(self, msg, finish_reason="stop"):
            self.choices = [type("C", (), {"message": msg, "finish_reason": finish_reason})()]

    class Completions:
        def __init__(self):
            self.n = 0

        def create(self, **kwargs):
            self.n += 1
            if self.n == 1:
                return Resp(Msg(content="", tool_calls=[TC()]), "tool_calls")
            return Resp(Msg(content="should not guess a plan"), "stop")

    completions = Completions()
    client = type("C", (), {"chat": type("H", (), {"completions": completions})()})()
    n_impl = {"n": 0}

    def impl(**_kw):
        n_impl["n"] += 1
        from cre_rentroll.turn_ctx import record

        result = {"ok": True, "answer": clarify, "source": "vacancy-prediction-agent"}
        record("ask_prediction_agent", {"question": "What occupancy do you predict?"}, result)
        return json.dumps(result)

    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(R=5, G=5, request_C=5, vault_calc_ok=True, judge="llm"),
    )
    token = bind(lambda *_a, **_k: None)
    try:
        draft = _run_openai_tools_loop(
            client,
            "gpt-4o-mini",
            [{"type": "function", "function": {"name": "ask_prediction_agent"}}],
            [{"role": "user", "content": "What occupancy do you predict?"}],
            {"ask_prediction_agent": impl},
        )
    finally:
        reset(token)
    assert clarify in draft
    assert completions.n == 1
    assert n_impl["n"] == 1
    assert "should not guess" not in draft

