from cre_rentroll.harness import run_task
from cre_rentroll.run_events import (
    emit,
    emit_stop_and_summary,
    tool_message,
    bind,
    reset,
    cloudwatch_only,
    labeled,
    annotate_message,
    mark_llm,
)
from cre_rentroll.schemas import CanonicalRentRoll, EvalScores, UnitRow
from cre_rentroll.tools import create_plan, reflection, update_plan, update_plan_status
from cre_rentroll.turn_ctx import (
    add_llm,
    llm_call_count,
    llm_call_kinds,
    llm_usage,
    parse_openai_usage,
    record,
    reset_turn,
    set_checklist,
)


def test_score_gcr_tool_message_lists_invented_numbers():
    line = tool_message(
        "score_gcr",
        {"draft": "Occupancy is 92.1%."},
        {
            "G": 5,
            "request_C": 5,
            "R": 5,
            "vault_calc_ok": None,
            "vault_invented": ["92.1%"],
            "judge": "llm",
            "decision": "ok",
        },
    )
    assert "vault_calc=N/A" in line
    assert "mismatch" not in line
    bogus = tool_message(
        "score_gcr",
        {"draft": "NOI is 12400000"},
        {
            "G": 5,
            "request_C": 5,
            "R": 5,
            "vault_calc_ok": None,
            "vault_invented": ["12400000"],
            "judge": "llm",
            "decision": "ok",
        },
    )
    assert "vault_calc=N/A" in bogus
    assert "mismatch" not in bogus
    why = "NOI 12400000 is not in vault"
    with_reason = tool_message(
        "score_gcr",
        {"draft": "NOI is 12400000"},
        {
            "G": 5,
            "request_C": 5,
            "R": 5,
            "vault_calc_ok": None,
            "vault_invented": ["12400000"],
            "judge": "llm",
            "decision": "ok",
            "reason": why,
        },
    )
    assert "vault_calc=N/A" in with_reason
    assert "mismatch" not in with_reason
    assert "need_replan" not in with_reason
    low = tool_message(
        "score_gcr",
        {"draft": "Weather is nice."},
        {
            "G": 1,
            "request_C": 2,
            "R": 5,
            "vault_calc_ok": True,
            "vault_invented": [],
            "judge": "llm",
            "decision": "need_replan",
            "notes": "answer ignores the rent-roll ask",
        },
    )
    assert "failed G=1, request-C=2" in low
    assert "answer ignores the rent-roll ask" in low
    ok = tool_message(
        "score_gcr",
        {"draft": "Occupancy is 92.07%."},
        {
            "G": 5,
            "request_C": 5,
            "R": 5,
            "vault_calc_ok": True,
            "vault_invented": [],
            "judge": "llm",
            "decision": "ok",
        },
    )
    assert "vault_calc=ok" in ok
    assert "not in vault spans" not in ok


def test_tool_message_summarizes_without_grid():
    line = tool_message(
        "query_units",
        {},
        {
            "unit_count": 198,
            "occupied_count": 180,
            "vacant_count": 18,
            "mtm_count": 0,
            "units": [{"unit_id": "1"}] * 10,
        },
    )
    assert line == "query_units (MCP) → 198 units, occupied 180, vacant 18, MTM 0"
    assert "unit_id" not in line
    expected = "query_units (MCP) → 164 units, occupied 151, vacant 13, MTM 0"
    step8 = tool_message(
        "query_units",
        {},
        {
            "unit_count": 164,
            "occupied_count": 151,
            "vacant_count": 13,
            "mtm_count": 0,
        },
    )
    assert step8 == expected
    vacant = tool_message(
        "query_units",
        {"vacant_only": True},
        {
            "unit_count": 164,
            "occupied_count": 151,
            "vacant_count": 13,
            "mtm_count": 0,
            "units": [{"unit_id": "v"}] * 13,
        },
    )
    assert vacant == "query_units (MCP) vacant_only → 13 vacant (of 164), MTM 0"
    occ = tool_message(
        "query_units",
        {"occupancy": "Vacant"},
        {
            "unit_count": 164,
            "occupied_count": 151,
            "vacant_count": 13,
            "mtm_count": 0,
        },
    )
    assert occ == "query_units (MCP) occupancy=Vacant → 13 vacant (of 164), MTM 0"
    full_only = tool_message("query_units", {}, {"unit_count": 198, "units": [{"unit_id": "1"}]})
    assert full_only == "query_units (MCP) → 198 units"
    inc = tool_message(
        "compute_income",
        {},
        {
            "in_place_monthly": 80019,
            "occupied_count": 151,
            "vacant_count": 13,
            "mtm_count": 0,
        },
    )
    assert inc == "compute_income (MCP) → monthly 80019, occupied 151, vacant 13, MTM 0"
    assert "compute_income (MCP)" in inc
    tasks = (
        "1. Query unit count and vacant count. 2. Compute occupancy percent and "
        "in-place income. 3. Draft the abstract with citations."
    )
    plan = tool_message("create_plan", {"tasks": tasks}, {"plan": tasks, "ok": True})
    assert "…" not in plan
    assert "citations" in plan
    assert tasks in plan
    long_q = "multifamily occupancy and vacant count for Hill Street " + ("x" * 80)
    search = tool_message("web_search", {"query": long_q}, {"results": [{}, {}]})
    assert long_q in search
    assert "…" not in search
    analysis = "Need occupancy from query_units then compute_income before drafting. " * 8
    ref = tool_message(
        "reflection",
        {"analysis": analysis, "need_replan": False},
        {"analysis": analysis, "need_replan": False},
    )
    assert analysis.strip() in ref
    assert "…" not in ref


def test_emit_skills_attached_is_not_llm_or_tool():
    seen = []
    token = bind(seen.append)
    try:
        emit(
            "skills",
            "attached rent-roll-abstract, citation-policy, underwriting",
            skills=["rent-roll-abstract", "citation-policy", "underwriting"],
        )
    finally:
        reset(token)
    assert len(seen) == 1
    assert seen[0]["step"] == "skills"
    assert seen[0]["message"] == "attached rent-roll-abstract, citation-policy, underwriting"
    assert "(LLM)" not in seen[0]["message"]
    assert "(skill)" not in seen[0]["message"]
    assert "used skill" not in seen[0]["message"]


def test_emit_writes_cloudwatch_line():
    import logging

    log = logging.getLogger("cre.run")
    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    handler = Capture()
    log.addHandler(handler)
    token = bind(lambda _p: None)
    try:
        emit("react", "ReAct turn 1 — create_plan", llm=True, tools=["create_plan"])
    finally:
        reset(token)
        log.removeHandler(handler)
    assert any(x.startswith("RUN react ") and "create_plan" in x for x in lines)


def test_emit_reaches_bound_sink():
    seen = []
    token = bind(seen.append)
    try:
        emit("plan", "RollAbstractor — single-intent", inventories=["RollAbstractor"])
    finally:
        reset(token)
    assert len(seen) == 1
    assert seen[0]["step"] == "plan"
    assert seen[0]["message"] == "RollAbstractor (skill) — single-intent"
    assert seen[0]["data"]["inventories"] == ["RollAbstractor"]


def test_failed_tool_emits_error_and_replan():
    seen = []
    token = bind(seen.append)
    reset_turn("tester")
    try:
        record("web_search", {"query": "x"}, {"ok": False, "error": "forced failure"})
    finally:
        reset(token)
    steps = [row["step"] for row in seen]
    assert "tool" in steps
    assert "error" in steps
    assert "reflect" not in steps
    assert "replan" in steps
    assert any("Replan" in (row.get("message") or "") for row in seen)
    assert any("web_search (MCP)" in (row.get("message") or "") for row in seen)


def test_stop_summary_counts_mcp_skills_retries():
    seen = []
    token = bind(seen.append)
    reset_turn("tester")
    try:
        emit_stop_and_summary(
            "eval_ok",
            traces=[
                {"name": "query_units", "args": {}, "result": {}},
                {"name": "compute_income", "args": {}, "result": {}},
            ],
            retry_n=1,
            token_text="x" * 40,
            skills=["rent-roll-abstract", "citation-policy"],
            started=__import__("time").time() - 1.5,
        )
    finally:
        reset(token)
    steps = [row["step"] for row in seen]
    assert steps[-2:] == ["stop", "summary"]
    msg = seen[-1]["message"]
    assert "0 LLM calls" in msg
    assert "2 MCP" in msg
    assert "query_units (MCP)" in msg
    assert "2 skills" in msg
    assert "rent-roll-abstract (skill)" in msg
    assert "citation-policy (skill)" in msg
    assert "other tool" not in msg
    assert "1 retry" in msg
    assert "in 0 tok · out 0 tok" in msg
    assert "~" not in msg.split("confidence")[0]
    assert "confidence" in msg


def test_stop_summary_splits_openai_input_output_tokens():
    seen = []
    token = bind(seen.append)
    reset_turn("tester")
    add_llm("scope", prompt_tokens=100, completion_tokens=20)
    add_llm("agent", prompt_tokens=1000, completion_tokens=400)
    add_llm("judge", prompt_tokens=134, completion_tokens=147)
    try:
        emit_stop_and_summary("eval_ok", traces=[], retry_n=0, skills=[])
    finally:
        reset(token)
    msg = seen[-1]["message"]
    assert "in 1234 tok · out 567 tok" in msg
    assert "~" not in msg.split("confidence")[0]
    data = seen[-1]["data"]
    assert data["tokens_in"] == 1234
    assert data["tokens_out"] == 567


def test_stop_summary_unknown_when_usage_missing():
    seen = []
    token = bind(seen.append)
    reset_turn("tester")
    add_llm("agent")
    try:
        emit_stop_and_summary("eval_ok", traces=[], retry_n=0, skills=[])
    finally:
        reset(token)
    msg = seen[-1]["message"]
    assert "in/out unknown" in msg
    assert "tokens_in" not in seen[-1]["data"]
    assert "tokens_out" not in seen[-1]["data"]


def test_parse_openai_usage_prompt_and_completion():
    class _Usage:
        prompt_tokens = 11
        completion_tokens = 7

    class _Resp:
        usage = _Usage()

    assert parse_openai_usage(_Resp()) == (11, 7)
    assert parse_openai_usage({"usage": {"input_tokens": 3, "output_tokens": 4}}) == (3, 4)
    assert parse_openai_usage({}) == (None, None)


def test_stop_summary_confidence_na_when_judge_not_run():
    seen = []
    token = bind(seen.append)
    try:
        emit_stop_and_summary(
            "eval_error",
            traces=[],
            retry_n=0,
            scores=EvalScores(judge="not_run"),
            token_text="x",
            skills=[],
        )
    finally:
        reset(token)
    msg = seen[-1]["message"]
    assert "confidence n/a (judge not run)" in msg
    assert "R=1" not in msg
    assert "vault mismatch" not in msg


def test_stop_summary_confidence_na_out_of_scope():
    seen = []
    token = bind(seen.append)
    try:
        emit_stop_and_summary(
            "out of scope",
            traces=[],
            retry_n=0,
            scores=EvalScores(judge="not_run"),
            token_text="What's your name",
            skills=[],
        )
    finally:
        reset(token)
    msg = seen[-1]["message"]
    assert "confidence n/a (out of scope)" in msg
    assert "judge not run" not in msg


def test_mark_llm_is_idempotent():
    assert mark_llm("CRE abstract") == "CRE abstract (LLM)"
    assert mark_llm("CRE abstract (LLM)") == "CRE abstract (LLM)"
    assert labeled("query_units", "LLM") == "query_units (LLM)"


def test_annotate_labels_skills_mcp_and_local_tools():
    assert labeled("RollAbstractor") == "RollAbstractor (skill)"
    assert labeled("Locator") == "Locator (skill)"
    assert labeled("Underwriter") == "Underwriter (skill)"
    assert labeled("query_units") == "query_units (MCP)"
    assert labeled("web_search") == "web_search (MCP)"
    line = annotate_message("RollAbstractor / Locator / Underwriter")
    assert line == "RollAbstractor (skill) / Locator (skill) / Underwriter (skill)"
    already = annotate_message("query_units (MCP) → 198 units")
    assert already == "query_units (MCP) → 198 units"
    mixed = annotate_message("osm_geocode then query_units")
    assert mixed == "osm_geocode (MCP) then query_units (MCP)"


def test_harness_empty_draft_one_run_agent_not_python_inject(monkeypatch):
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    seen: list[dict] = []
    token = bind(seen.append)
    n = {"n": 0}

    def fake_agent(*_a, **_k):
        n["n"] += 1
        return ""

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    try:
        run_task("tester", "What is occupancy and in-place income?", skip_scope=True)
    finally:
        reset(token)

    assert n["n"] == 1
    assert not any("second ReAct pass" in (row.get("message") or "") for row in seen)
    assert not any(
        row["step"] == "tool" and "compute_income" in row["message"] for row in seen
    )
    assert not any("harness injection" in (row["message"] or "").lower() for row in seen)
    reflect_msgs = [row["message"] for row in seen if row["step"] == "reflect"]
    assert not any("empty draft" in m and "RollAbstractor" in m for m in reflect_msgs)
    assert not any("incomplete" in m.lower() and "occupancy" in m.lower() for m in reflect_msgs)
    assert not any("Carry citations" in m for m in reflect_msgs)
    drafts = [row["message"] for row in seen if row["step"] == "draft"]
    assert any("agent draft is empty" in m for m in drafts)
    assert not any("second agent draft" in m for m in drafts)
    assert not any(m.startswith("Harness vault fill:") for m in drafts)
    assert any("Draft before eval is empty" in m for m in drafts)
    eval_msgs = [row["message"] for row in seen if row["step"] == "eval"]
    assert any("judge=not_run" in m and "→ error" in m for m in eval_msgs)
    assert any("G=N/A C=N/A R=N/A vault_calc=N/A judge=not_run" in m for m in eval_msgs)
    assert not any("G=5" in m for m in eval_msgs)
    assert not any("G=1" in m and "judge=not_run" in m for m in eval_msgs)
    summaries = [row["message"] for row in seen if row["step"] == "summary"]
    assert any("confidence n/a (judge not run)" in m for m in summaries)


def test_harness_judge_failure_is_eval_error(monkeypatch):
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    seen: list[dict] = []
    token = bind(seen.append)
    from cre_rentroll.tools import score_gcr

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")

    def fake_agent(*_a, **_k):
        score_gcr("Occupancy is 92.07%.")
        return "Occupancy is 92.07%."

    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(vault_calc_ok=True, judge="error"),
    )
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    try:
        outcome = run_task("tester", "What is occupancy?", skip_scope=True)
    finally:
        reset(token)

    assert outcome.kind == "error"
    assert outcome.stop_reason == "eval_error"
    assert outcome.eval is not None
    assert outcome.eval.judge == "error"
    assert outcome.eval.G is None and outcome.eval.R is None and outcome.eval.request_C is None
    assert "92.07" not in (outcome.text or "")
    assert "could not finish that answer this turn" in (outcome.text or "").lower()
    eval_msgs = [row["message"] for row in seen if row["step"] == "eval"]
    assert any("judge=error" in m for m in eval_msgs)
    assert not any("(LLM)" in m for m in eval_msgs)
    assert not any(row["step"] == "retry" for row in seen)


def test_create_plan_and_reflection_messages_tagged_llm_not_compute_income():
    seen = []
    token = bind(seen.append)
    reset_turn("tester")
    try:
        create_plan("1. query_units\n2. compute_income")
        reflection("need occupancy before RollAbstract")
        record(
            "compute_income",
            {},
            {"in_place_monthly": 1500.0, "vacant_count": 0, "mtm_count": 0},
        )
    finally:
        reset(token)
    tool_msgs = [row["message"] for row in seen if row["step"] == "tool"]
    create_line = next(m for m in tool_msgs if "create_plan" in m)
    reflect_line = next(m for m in tool_msgs if "reflection" in m)
    income_line = next(m for m in tool_msgs if m.startswith("compute_income"))
    assert "(LLM)" in create_line
    assert "query_units" in create_line
    assert "initial checklist" in create_line
    assert "(LLM)" in reflect_line
    assert "need occupancy" in reflect_line
    assert "compute_income (MCP)" in income_line
    assert "(LLM)" not in income_line


def test_emit_react_appends_matching_checklist_task():
    reset_turn("tester")
    set_checklist(
        [
            {"text": "query_units", "status": "completed"},
            {"text": "Compute income for in-place monthly rent.", "status": "pending"},
        ]
    )
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        emit("react", "ReAct turn 4 — compute_income", llm=True, turn=4, tools=["compute_income"], stop="tool_use")
        emit("react", "ReAct turn 4 — compute_income stop=tool_use", llm=True, tools=["compute_income"], stop="tool_use")
        emit("react", "ReAct turn 5 — score_gcr", llm=True, tools=["score_gcr"])
        emit("react", "ReAct turn 1 — create_plan", llm=True, tools=["create_plan"])
        emit("react", "ReAct turn 6 — osm_geocode", llm=True, tools=["osm_geocode"])
    finally:
        reset(token)
    assert seen[0]["message"] == (
        "ReAct turn 4 — compute_income (MCP) — for 2. [pending] "
        "Compute income for in-place monthly rent. (LLM)"
    )
    assert seen[1]["message"] == (
        "ReAct turn 4 — compute_income (MCP) — for 2. [pending] "
        "Compute income for in-place monthly rent. stop=tool_use (LLM)"
    )
    assert "for " not in seen[2]["message"]
    assert "for " not in seen[3]["message"]
    assert "for (no matching task)" in seen[4]["message"]


def test_update_plan_run_log_keeps_checklist_diff():
    seen = []
    token = bind(seen.append)
    reset_turn("tester")
    try:
        create_plan("1. [in_progress] query_units\n2. [pending] compute_income")
        update_plan_status("1. [x] query_units\n2. **in_progress** compute_income")
    finally:
        reset(token)
    tool_msgs = [row["message"] for row in seen if row["step"] == "tool"]
    update = next(m for m in tool_msgs if "checklist updated" in m)
    assert "completed: query_units" in update
    assert "[completed] query_units" in update
    assert "[in_progress] compute_income" in update
    plan_msgs = [row["message"] for row in seen if row["step"] == "plan"]
    assert any("checklist updated" in m and "completed: query_units" in m for m in plan_msgs)


def test_update_plan_run_log_shows_replaced_list():
    seen = []
    token = bind(seen.append)
    reset_turn("tester")
    try:
        create_plan("1. [in_progress] web_search property metro")
        update_plan("1. [in_progress] web_search San Francisco Bay Area cap rates")
    finally:
        reset(token)
    joined = "\n".join(row["message"] for row in seen)
    assert "update_plan (MCP)" in joined
    assert "Bay Area" in joined
    assert "Plan replaced" in joined or "kept completed" in joined.lower() or "checklist updated" in joined or "new:" in joined.lower()


def test_harness_mocked_agent_judge_count_and_tags(monkeypatch):
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    seen: list[dict] = []
    token = bind(seen.append)
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    from cre_rentroll.tools import score_gcr

    def fake_agent(*_a, **_k):
        draft = '{"occupancy_pct": 1.0, "in_place_monthly": 1500}'
        score_gcr(draft)
        return draft

    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(R=5, G=5, request_C=5, vault_calc_ok=True, judge="llm"),
    )
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    try:
        run_task("tester", "What is occupancy and in-place income?")
    finally:
        reset(token)

    msgs = [row["message"] for row in seen]
    assert not any(row["step"] == "scope" for row in seen)
    assert not any("in-loop (ReAct prompt)" in m for m in msgs)
    assert not any("This is a rent-roll abstract question" in m for m in msgs)
    assert any(
        row["step"] == "llm" and "starting" in row["message"].lower() and "(LLM)" in row["message"]
        for row in seen
    )
    assert any(
        row["step"] == "llm" and "finished" in row["message"].lower() and "(LLM)" in row["message"]
        for row in seen
    )
    drafts = [row["message"] for row in seen if row["step"] == "draft"]
    assert any("agent draft" in m and "(LLM)" in m for m in drafts)
    assert any(m.startswith("Draft before eval:") and "(LLM)" not in m for m in drafts)
    eval_msgs = [row["message"] for row in seen if row["step"] == "eval"]
    assert any("G=5" in m and "judge=llm" in m and "(LLM)" in m for m in eval_msgs)
    summary = next(row["message"] for row in seen if row["step"] == "summary")
    assert "2 LLM (agent×1, judge)" in summary
    assert llm_call_kinds().count("scope") == 0
    assert llm_call_kinds().count("agent") == 1
    assert llm_call_kinds().count("judge") == 1
    assert llm_call_count() == 2
    inv_msgs = [row["message"] for row in seen if row["step"] == "inventory"]
    plan_msgs = [row["message"] for row in seen if row["step"] == "plan"]
    assert any("(LLM)" not in m and "full tool menu" in m for m in inv_msgs)
    assert not any("RollAbstractor" in m for m in inv_msgs)
    assert not any("RollAbstractor" in m for m in plan_msgs)
    assert not any("skip decompose" in m for m in plan_msgs)
    skill_rows = [row for row in seen if row["step"] == "skills"]
    assert len(skill_rows) == 1
    assert skill_rows[0]["message"] == (
        "attached rent-roll-abstract, citation-policy, underwriting"
    )
    assert "(LLM)" not in skill_rows[0]["message"]
    assert not any("used skill" in (row.get("message") or "").lower() for row in seen)
    assert not any(
        row["step"] == "tool"
        and any(
            name in (row.get("message") or "")
            for name in ("rent-roll-abstract", "citation-policy", "underwriting")
        )
        for row in seen
    )
    inv_i = next(i for i, row in enumerate(seen) if row["step"] == "inventory")
    skills_i = next(i for i, row in enumerate(seen) if row["step"] == "skills")
    react_is = [i for i, row in enumerate(seen) if row["step"] == "react"]
    assert skills_i == inv_i + 1
    assert not react_is or skills_i < react_is[0]


def test_cloudwatch_only_does_not_fan_out_to_sse_sink():
    seen = []
    token = bind(seen.append)
    try:
        emit("inventory", "should reach sink")
        with cloudwatch_only():
            emit("ingest", "La Cordoba Rent Roll, 164 units")
            emit("agentcore_memory", "wrote conversational STM")
        emit("react", "after ingest")
    finally:
        reset(token)
    assert [row["step"] for row in seen] == ["inventory", "react"]


def test_ingest_only_chat_does_not_fill_sse_logs(monkeypatch):
    from cre_rentroll.main import _run_chat_sync
    from cre_rentroll.schemas import ChatOutcome

    seen = []

    def fake_ingest(user, filename, raw):
        emit("ingest", "La Cordoba Rent Roll, 164 units, parser=realpage")
        emit("agentcore_memory", "wrote conversational STM")
        return ChatOutcome(kind="ingest_ack", text="ok", stop_reason="ingest")

    monkeypatch.setattr("cre_rentroll.harness.ingest_attachment", fake_ingest)
    monkeypatch.setattr(
        "cre_rentroll.main._decorate_outcomes",
        lambda user, outcomes: dict(outcomes[-1]),
    )
    token = bind(seen.append)
    try:
        _run_chat_sync("tester", "", False, True, "roll.csv", b"abc")
    finally:
        reset(token)
    assert seen == []


def test_question_submit_still_streams_ingest_then_react(monkeypatch):
    from cre_rentroll.main import _run_chat_sync
    from cre_rentroll.schemas import ChatOutcome

    seen = []

    def fake_ingest(user, filename, raw):
        emit("ingest", "La Cordoba Rent Roll, 164 units, parser=realpage")
        return ChatOutcome(kind="ingest_ack", text="ok", stop_reason="ingest")

    def fake_task(user, text, search_fail=False, has_attachment=False, chip_id=""):
        emit("inventory", "full tool menu (book)")
        emit("react", "ReAct turn 1")
        return ChatOutcome(kind="answer", text="164 units", stop_reason="eval_ok")

    monkeypatch.setattr("cre_rentroll.harness.ingest_attachment", fake_ingest)
    monkeypatch.setattr("cre_rentroll.harness.run_task", fake_task)
    monkeypatch.setattr(
        "cre_rentroll.main._decorate_outcomes",
        lambda user, outcomes: dict(outcomes[-1]),
    )
    token = bind(seen.append)
    try:
        _run_chat_sync("tester", "What's occupancy?", False, True, "roll.csv", b"abc")
    finally:
        reset(token)
    assert [row["step"] for row in seen] == ["ingest", "inventory", "react"]


def test_keep_run_preserves_llm_call_counts():
    reset_turn("tester")
    add_llm("scope", prompt_tokens=10, completion_tokens=2)
    add_llm("agent", prompt_tokens=20, completion_tokens=3)
    reset_turn("tester", keep_run=True)
    add_llm("agent", prompt_tokens=30, completion_tokens=4)
    add_llm("judge", prompt_tokens=40, completion_tokens=5)
    assert llm_call_kinds() == ["scope", "agent", "agent", "judge"]
    assert llm_usage() == [(10, 2), (20, 3), (30, 4), (40, 5)]
    reset_turn("tester")
    assert llm_call_count() == 0
    assert llm_usage() == []
