import cre_rentroll.scope as scope_mod
from cre_rentroll.agents import BOOK
from cre_rentroll.harness import run_task
from cre_rentroll.run_events import bind, reset
from cre_rentroll.scope import OOS_TEXT, classify_scope, in_scope, parse_scope_token
from cre_rentroll.turn_ctx import traces

_OOS_TOKENS = {
    "What's the weather this weekend?": "OOS",
    "What's your name": "OOS",
    "How are you today": "OOS",
    "Hello": "OOS",
    "who are you": "OOS",
}
_IN_TOKENS = {
    "what's the population in West Las Vegas": "IN_SCOPE",
    "what the flood risk in that area?": "IN_SCOPE",
    "What's a rent roll?": "IN_SCOPE",
    "What is occupancy?": "IN_SCOPE",
    "What is occupancy and in-place income?": "IN_SCOPE",
    "Abstract this rent roll": "IN_SCOPE",
    "Locate this property on the map": "IN_SCOPE",
    "what if all occupied?": "IN_SCOPE",
    "What street is that at?": "IN_SCOPE",
    "what coordinates again?": "IN_SCOPE",
    "Where is this property? Can you put it on a map, and how many units are vacant, and what is occupancy?": "IN_SCOPE",
    "What is in-place monthly income, and what would annualized NOI be if opex is 35% of EGI?": "IN_SCOPE",
    "Put this property on a map, how many units are month-to-month, and what cap rate is implied at a $12M ask?": "IN_SCOPE",
}


def _inject_token(monkeypatch, token: str):
    monkeypatch.setattr(
        "cre_rentroll.scope._scope_agent_token",
        lambda message, history=None: token,
    )


def test_classify_prompt_is_generic_property_deal_area_vs_personal_chat():
    blob = scope_mod._SCOPE_SYS.lower()
    assert "in_scope" in blob
    assert "property" in blob
    assert "deal" in blob
    assert "area" in blob or "market" in blob
    assert "personal" in blob or "idle" in blob
    assert "how are you" in blob
    assert "web_search" in blob
    assert "no tools" in blob
    assert "oos only" in blob
    assert "flood" not in blob
    assert "population" not in blob
    assert "las vegas" not in blob
    assert not hasattr(scope_mod, "_CRE")


def test_live_scope_module_uses_agent_token_not_regex():
    assert hasattr(scope_mod, "classify_scope")
    assert callable(classify_scope)
    assert not hasattr(scope_mod, "apply_scope_gate")
    assert not hasattr(scope_mod, "ScopeDecision")
    assert not hasattr(scope_mod, "_CRE")
    assert not hasattr(scope_mod, "_ALWAYS_OOS")
    assert parse_scope_token("IN_SCOPE") is True
    assert parse_scope_token("OOS") is False
    assert parse_scope_token("garbage") is False
    assert parse_scope_token("") is False


def test_injected_agent_tokens_drive_in_scope(monkeypatch):
    for q, token in {**_OOS_TOKENS, **_IN_TOKENS}.items():
        _inject_token(monkeypatch, token)
        assert in_scope(q) is (token == "IN_SCOPE")
        assert classify_scope(q) is (token == "IN_SCOPE")


def test_map_mtm_cap_multipart_ask_is_in_scope(monkeypatch):
    _inject_token(monkeypatch, "IN_SCOPE")
    assert in_scope(
        "Put this property on a map, how many units are month-to-month, and what cap rate is implied at a $12M ask?"
    )


def _stub_vault(monkeypatch):
    from cre_rentroll.schemas import EvalScores

    monkeypatch.setattr("cre_rentroll.harness.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.harness.vault.load_roll", lambda u: None)
    monkeypatch.setattr("cre_rentroll.harness.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(G=5, request_C=5, R=5, judge="heuristic"),
    )


def _forbid_mcp(monkeypatch):
    def _no_begin(*_a, **_k):
        raise AssertionError("begin_turn must not run on out-of-scope asks")

    def _no_gateway(*_a, **_k):
        raise AssertionError("gateway_call must not run on out-of-scope asks")

    def _no_memory(*_a, **_k):
        raise AssertionError("AgentCore Memory must not run on out-of-scope asks")

    monkeypatch.setattr("cre_rentroll.agentcore_platform.begin_turn", _no_begin)
    monkeypatch.setattr("cre_rentroll.agentcore_platform.gateway_call", _no_gateway)
    monkeypatch.setattr("cre_rentroll.agentcore_platform.facts_from_memory", _no_memory)


def _run_oos_task(monkeypatch, question: str, draft: str):
    _stub_vault(monkeypatch)
    _forbid_mcp(monkeypatch)
    _inject_token(monkeypatch, "OOS")
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    captured: dict = {"n": 0, "classify": 0}
    real_classify = classify_scope

    def wrapped(message, history=None):
        captured["classify"] += 1
        return real_classify(message, history)

    monkeypatch.setattr("cre_rentroll.harness.classify_scope", wrapped)

    def fake_agent(_prompt, inv, _facts):
        captured["n"] += 1
        captured["inv"] = inv
        return draft

    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    seen = []
    token = bind(seen.append)
    try:
        out = run_task("analyst", question)
    finally:
        reset(token)
    return out, captured, seen


def test_run_task_greeting_agent_oos(monkeypatch):
    out, captured, seen = _run_oos_task(monkeypatch, "Hello", OOS_TEXT)
    assert captured["n"] == 1
    assert captured["inv"].tool_names == ()
    assert captured["inv"].skills == ()
    assert out.text == OOS_TEXT  # stub LLM copied the prompt sentence; Python does not swap it in
    assert out.tools_called == []
    assert traces() == []
    assert not any(row["step"] == "scope" for row in seen)
    assert not any("in-loop (ReAct prompt)" in row["message"] for row in seen)
    summary = next(row["message"] for row in seen if row["step"] == "summary")
    assert "scope," not in summary
    assert "agent" in summary


def test_run_task_need_file_vault_gate_no_extra_llm(monkeypatch):
    _stub_vault(monkeypatch)
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    from cre_rentroll.tools import query_units

    captured: dict[str, str] = {}

    def fake_agent(*_a, **_k):
        captured["t"] = query_units()
        return captured["t"]

    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    seen = []
    token = bind(seen.append)
    try:
        out = run_task("analyst", "Abstract this rent roll")
    finally:
        reset(token)
    assert out.kind == "warning"
    assert out.stop_reason == "need_attachment"
    assert out.text == captured["t"]
    kinds = [row["step"] for row in seen]
    assert kinds.count("scope") == 1
    scope_ev = next(row for row in seen if row["step"] == "scope")
    assert "scope (agent)" in scope_ev["message"]
    assert "need rent-roll" in scope_ev["message"]
    assert "(LLM)" not in scope_ev["message"]
    summary = next(row["message"] for row in seen if row["step"] == "summary")
    assert "scope," not in summary


def test_run_task_oos_keeps_llm_wording(monkeypatch):
    draft = (
        "This agent answers questions about the property, the deal, or the area it "
        "sits in — not personal chat. This app will not forecast weekend weather."
    )
    out, captured, seen = _run_oos_task(
        monkeypatch, "What's the weather this weekend?", draft
    )
    assert out.text == draft
    assert out.text != OOS_TEXT
    assert captured["inv"].tool_names == ()
    assert captured["inv"].skills == ()
    assert out.tools_called == []
    assert traces() == []
    assert not any(row.get("tools") for row in seen)
    assert not any(
        (row.get("step") or "") in {"react", "mcp"}
        or "web_search" in (row.get("message") or "")
        or "create_plan" in (row.get("message") or "")
        or "score_gcr" in (row.get("message") or "")
        for row in seen
    )


def test_run_task_oos_strips_planner_leak_not_shown(monkeypatch):
    leak = "I will conduct a search to define a rent roll.\n[tag: in_progress] [pending]"
    out, captured, _seen = _run_oos_task(monkeypatch, "What's the weather this weekend?", leak)
    assert captured["inv"].tool_names == ()
    assert "I will conduct a search" not in out.text
    assert "[tag: in_progress]" not in out.text
    assert "[pending]" not in out.text
    assert "could not finish" in out.text.lower()


def test_run_task_calls_classify_scope_then_empty_inventory(monkeypatch):
    out, captured, _seen = _run_oos_task(monkeypatch, "What's the weather?", OOS_TEXT)
    assert captured["classify"] == 1
    assert out.text == OOS_TEXT
    assert out.eval is not None
    assert captured["inv"].tool_names == ()


def test_system_prompt_identity_chitchat_is_oos_not_interview_wording():
    from cre_rentroll.agents import BOOK, system_prompt

    has = system_prompt(BOOK, "VAULT HAS a rent roll for this session.\n").lower()
    empty = system_prompt(BOOK, "VAULT EMPTY: no rent roll is ingested.\n").lower()
    for blob in (has, empty):
        assert "identity" in blob
        assert "agent's name" in blob or "the agent's name" in blob
        assert "chitchat" in blob
        assert "answer without tools" in blob
        assert "no create_plan" in blob
        assert "property" in blob and "deal" in blob and "area" in blob
        assert "personal chat" in blob
        assert "flood" not in blob
        assert "population" not in blob
        assert "what's your name" not in blob
        assert "who are you" not in blob
        assert "i am cre" not in blob
    assert "never emit" in has and "out-of-scope refuse" in has
    assert "units, occupancy, income" in has or "occupancy" in has
    assert "average property value" in empty
    assert "web_search" in empty
    assert "never" in empty and "need-file" in empty
    assert "please attach a rent-roll csv" in empty
    # NEED_FILE_TEXT is occupancy/this-roll only — not the instruction for area value.
    need = empty.find("please attach a rent-roll csv")
    area = empty.find("average property value")
    assert need != -1 and area != -1
    after_area = empty[area : area + 400]
    assert "never" in after_area and "need-file" in after_area
    assert "web_search" in after_area
    assert "please attach a rent-roll csv" not in after_area


def _assert_oos_no_tools(out, captured, seen, text: str):
    assert captured["n"] == 1
    assert captured["inv"].tool_names == ()
    assert captured["inv"].skills == ()
    assert captured["inv"].name == BOOK.name
    assert out.text == text
    assert out.kind == "out_of_scope"
    assert out.stop_reason == "out_of_scope"
    assert out.tools_called == []
    assert traces() == []
    summary = next(row["message"] for row in seen if row["step"] == "summary")
    assert "n/a (out of scope)" in summary
    assert not any(row["step"] == "scope" for row in seen)
    assert not any(row.get("tools") for row in seen)
    assert not any(
        name in (row.get("message") or "")
        for row in seen
        for name in (
            "web_search",
            "osm_geocode",
            "query_units",
            "create_plan",
            "score_gcr",
            "gateway",
        )
    )
    assert not any("memory_reuse" == row["step"] for row in seen)


def test_run_task_whats_your_name_agent_oos(monkeypatch):
    out, captured, seen = _run_oos_task(monkeypatch, "What's your name", OOS_TEXT)
    _assert_oos_no_tools(out, captured, seen, OOS_TEXT)
    assert "scope," not in next(row["message"] for row in seen if row["step"] == "summary")
    assert "multifamily cap rates" not in out.text.lower()
    assert "metro comps" not in out.text.lower()


def test_run_task_how_are_you_today_agent_oos(monkeypatch):
    out, captured, seen = _run_oos_task(monkeypatch, "How are you today", OOS_TEXT)
    _assert_oos_no_tools(out, captured, seen, OOS_TEXT)


def test_run_task_who_are_you_agent_oos(monkeypatch):
    out, captured, seen = _run_oos_task(monkeypatch, "who are you", OOS_TEXT)
    _assert_oos_no_tools(out, captured, seen, OOS_TEXT)


def test_run_task_in_scope_does_not_emit_scope(monkeypatch):
    from cre_rentroll.schemas import CanonicalRentRoll, UnitRow

    real = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    monkeypatch.setattr("cre_rentroll.harness.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.harness.vault.load_roll", lambda u: real)
    monkeypatch.setattr("cre_rentroll.harness.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    from cre_rentroll.schemas import EvalScores

    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(G=5, request_C=5, R=5, judge="heuristic"),
    )
    monkeypatch.setattr(
        "cre_rentroll.harness.run_agent",
        lambda *a, **k: "Occupancy is 100% with in-place monthly 1500.",
    )
    seen = []
    token = bind(seen.append)
    try:
        out = run_task("analyst", "What is occupancy and in-place income?")
    finally:
        reset(token)
    assert out.text == "Occupancy is 100% with in-place monthly 1500."
    assert out.text != OOS_TEXT
    assert not any(row["step"] == "scope" for row in seen)
    assert not any("in-loop (ReAct prompt)" in row["message"] for row in seen)
