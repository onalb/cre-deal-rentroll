"""Deal prediction stub posts model arguments to /mcp/deal. No vacancy MCP client."""

import inspect
import json

from cre_rentroll.agents import BOOK, _run_openai_tools_loop
from cre_rentroll.capabilities_client import GATEWAY_REQUIRED
from cre_rentroll.harness import run_task
from cre_rentroll.predict_followup import reset_predict_state
from cre_rentroll.run_events import bind, reset
from cre_rentroll.schemas import CanonicalRentRoll, EvalScores, UnitRow
from cre_rentroll.tools import TOOL_IMPLS, ask_prediction_agent
from cre_rentroll.turn_ctx import reset_turn


def test_prediction_tool_is_on_the_book_menu():
    assert "ask_prediction_agent" in BOOK.tool_names
    assert "ask_vacancy_agent" not in BOOK.tool_names
    assert "ask_vacancy" not in BOOK.tool_names
    assert TOOL_IMPLS["ask_prediction_agent"].__wrapped__ is ask_prediction_agent
    source = inspect.getsource(ask_prediction_agent)
    assert "RR_PREDICT_URL" not in source
    assert "def forecast" not in source
    assert "/mcp/deal" not in source
    assert "call_deal_tool" in inspect.getsource(__import__("cre_rentroll.tools", fromlist=["tools"]))


def test_ask_prediction_agent_requires_gateway_when_unset(monkeypatch):
    monkeypatch.delenv("MCP_URL", raising=False)
    monkeypatch.delenv("AGENTCORE_GATEWAY_URL", raising=False)
    monkeypatch.delenv("CAPABILITIES_GATEWAY_URL", raising=False)
    reset_turn("tester")

    def boom(*_a, **_k):
        raise AssertionError("gateway must not be called")

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", boom)
    payload = json.loads(ask_prediction_agent(12, "forecast occupancy"))
    assert payload["ok"] is False
    assert payload["error"] == GATEWAY_REQUIRED


def test_ask_prediction_agent_forwards_model_arguments(monkeypatch):
    monkeypatch.setenv("MCP_URL", "https://example.test/mcp")
    reset_turn("tester")
    seen: dict = {}

    def fake(name, arguments, timeout=180.0):
        seen["name"] = name
        seen["arguments"] = arguments
        return json.dumps(
            {
                "ok": True,
                "source": "vacancy-prediction-agent",
                "answer": "12 expirations",
                "horizon_months": arguments.get("horizon_months"),
                "awaiting_followup": False,
            }
        )

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)
    payload = json.loads(ask_prediction_agent(12, "forecast occupancy"))
    assert payload["answer"] == "12 expirations"
    assert seen["name"] == "ask_prediction_agent"
    assert seen["arguments"]["question"] == "forecast occupancy"
    assert seen["arguments"]["horizon_months"] == 12
    assert seen["arguments"]["username"] == "tester"
    assert "session_id" not in seen["arguments"]


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
    monkeypatch.setenv("MCP_URL", "https://example.test/mcp")
    monkeypatch.setattr(
        "cre_rentroll.mcp_gateway.list_tools",
        lambda timeout=30.0: ["ask_prediction_agent", "query_units", "create_plan"],
    )
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(R=4, G=4, request_C=4, vault_calc_ok=True, judge="llm"),
    )


def _gateway_answers(replies: list[str]):
    seen: list[dict] = []

    def fake(name, arguments, timeout=180.0):
        seen.append({"name": name, "arguments": dict(arguments)})
        answer = replies[len(seen) - 1]
        return json.dumps(
            {
                "ok": True,
                "source": "vacancy-prediction-agent",
                "answer": answer,
                "awaiting_followup": True,
            }
        )

    return fake, seen


def test_untrained_gateway_reply_is_the_book_answer(monkeypatch):
    reset_predict_state()
    monkeypatch.setenv("MCP_URL", "https://example.test/mcp")
    roll = _predict_roll()
    _stub_book_run(monkeypatch, roll)
    clarification = "That building is not trained. Trained properties: Alpha Court, Beta Place."
    fake, _seen = _gateway_answers([clarification])
    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)

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


def test_rate_limit_after_predictor_quotes_vacancy_text(monkeypatch):
    from cre_rentroll.agents import RATE_LIMIT_TEXT, RateLimitedError

    reset_predict_state()
    monkeypatch.setenv("MCP_URL", "https://example.test/mcp")
    roll = _predict_roll()
    _stub_book_run(monkeypatch, roll)
    vacancy = "Bay View Gardens Apartments is not trained. No properties are trained yet."
    fake, _seen = _gateway_answers([vacancy])
    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)

    def fake_agent(*_a, **_k):
        ask_prediction_agent(12, "What's the predicted vacancy for Bay View Gardens Apartments?")
        raise RateLimitedError()

    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    token = bind(lambda *_a, **_k: None)
    try:
        outcome = run_task(
            "tester",
            "What's the predicted vacancy for Bay View Gardens Apartments?",
            skip_scope=True,
            chip_id="predict_occupancy_only",
        )
    finally:
        reset(token)
    assert vacancy in (outcome.text or "")
    assert RATE_LIMIT_TEXT not in (outcome.text or "")


def test_followup_forwards_the_user_text(monkeypatch):
    reset_predict_state()
    monkeypatch.setenv("MCP_URL", "https://example.test/mcp")
    roll = _predict_roll()
    _stub_book_run(monkeypatch, roll)
    fake, seen = _gateway_answers(
        [
            "Which floor plan do you want?\n• Plan A\n• Plan B",
            "Plan A is vacant with probability 0.11 as of 2024-06-01.",
        ]
    )
    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)

    def first_agent(*_a, **_k):
        ask_prediction_agent(12, "What occupancy do you predict?")
        return "Occupancy is 50 percent."

    monkeypatch.setattr("cre_rentroll.harness.run_agent", first_agent)
    token = bind(lambda *_a, **_k: None)
    try:
        first = run_task("tester", "What occupancy do you predict?", skip_scope=True, chip_id="predict")
        assert "Which floor plan" in (first.text or "")

        def follow_agent(*_a, **_k):
            payload = json.loads(ask_prediction_agent(12, "Plan A"))
            return payload["answer"]

        monkeypatch.setattr("cre_rentroll.harness.run_agent", follow_agent)
        second = run_task("tester", "Plan A", skip_scope=True, chip_id="predict")
    finally:
        reset(token)
    assert seen[1]["arguments"]["question"] == "Plan A"
    assert seen[1]["name"] == "ask_prediction_agent"
    assert "0.11" in (second.text or "")


def test_digit_followup_skips_oos_and_calls_predictor(monkeypatch):
    from cre_rentroll.predict_followup import note_awaiting_followup

    reset_predict_state()
    note_awaiting_followup("tester", True)
    roll = _predict_roll()
    _stub_book_run(monkeypatch, roll)
    occupancy = "Predicted occupancy for 1x1 renovated = 0 is 18.2% as of 2024-06-01."
    fake, seen = _gateway_answers([occupancy])
    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)
    captured: dict = {}

    def boom_scope(*_a, **_k):
        raise AssertionError("scope hop must not run on a prediction follow-up")

    def fake_agent(_prompt, inv, _facts):
        captured["tools"] = inv.tool_names
        payload = json.loads(ask_prediction_agent(12, "0"))
        return payload["answer"]

    monkeypatch.setattr("cre_rentroll.harness.classify_scope", boom_scope)
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    token = bind(lambda *_a, **_k: None)
    try:
        outcome = run_task("tester", "0")
    finally:
        reset(token)
        reset_predict_state()
    assert "ask_prediction_agent" in captured["tools"]
    assert "create_plan" not in captured["tools"]
    assert seen[0]["arguments"]["question"] == "0"
    assert "18.2%" in (outcome.text or "")
    assert "create_plan" not in (outcome.text or "").lower()


def test_same_turn_second_ask_does_not_post(monkeypatch):
    reset_predict_state()
    reset_turn("tester")
    monkeypatch.setenv("MCP_URL", "https://example.test/mcp")
    calls = {"n": 0}

    def fake(name, arguments, timeout=180.0):
        calls["n"] += 1
        return json.dumps(
            {
                "ok": True,
                "source": "vacancy-prediction-agent",
                "answer": "building not trained",
                "awaiting_followup": True,
            }
        )

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)
    first = json.loads(ask_prediction_agent(12, "What occupancy do you predict?"))
    second = json.loads(ask_prediction_agent(12, "1x1 B"))
    third = json.loads(ask_prediction_agent(12, "2x2 G in La Cordoba"))
    assert calls["n"] == 1
    assert first["answer"] == "building not trained"
    assert second.get("same_turn_stop") is True
    assert third.get("same_turn_stop") is True
    assert second["answer"] == "building not trained"


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
