import inspect
import json

import pytest

from cre_rentroll.agents import BOOK, INVENTORIES
from cre_rentroll.harness import run_task, session_prompt
from cre_rentroll.run_events import annotate_message, bind, reset, tool_message
from cre_rentroll.schemas import CanonicalRentRoll, UnitRow
from cre_rentroll.tools import (
    TOOL_IMPLS,
    compute_income,
    create_plan,
    parse_task_items,
    query_units,
    react_turn_task_suffix,
    reflection,
    update_plan,
    update_plan_status,
)
from cre_rentroll.turn_ctx import checklist, llm_kind_ran, llm_plan, record, reset_turn, traces


def _roll() -> CanonicalRentRoll:
    return CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )


def test_react_tools_in_tool_impls():
    assert "create_plan" in TOOL_IMPLS
    assert "update_plan" in TOOL_IMPLS
    assert "update_plan_status" in TOOL_IMPLS
    assert "reflection" in TOOL_IMPLS
    assert "score_gcr" in TOOL_IMPLS
    assert TOOL_IMPLS["create_plan"] is create_plan
    assert TOOL_IMPLS["update_plan"] is update_plan
    assert TOOL_IMPLS["update_plan_status"] is update_plan_status
    assert TOOL_IMPLS["reflection"] is reflection
    for inv in INVENTORIES.values():
        assert "create_plan" in inv.tool_names
        assert "update_plan" in inv.tool_names
        assert "update_plan_status" in inv.tool_names
        assert "reflection" in inv.tool_names
        assert "score_gcr" in inv.tool_names
        assert "query_units" in inv.tool_names
        assert "compute_income" in inv.tool_names
        assert "osm_geocode" in inv.tool_names
        assert "publish_memo" in inv.tool_names


def test_book_tools_are_inprocess_mcp(monkeypatch):
    from cre_rentroll.mcp_runtime import (
        RENTROLL_MCP,
        OSM_MCP,
        VAULT_MCP,
        book_mcp_impls,
        mcp_server_for,
    )

    impls = book_mcp_impls()
    for name in BOOK.tool_names:
        assert name in impls
        assert callable(impls[name])
    assert mcp_server_for("query_units") == VAULT_MCP
    assert mcp_server_for("compute_cap_rate") == VAULT_MCP
    assert mcp_server_for("create_plan") == RENTROLL_MCP
    assert mcp_server_for("update_plan_status") == RENTROLL_MCP
    assert mcp_server_for("score_gcr") == RENTROLL_MCP
    assert mcp_server_for("osm_geocode") == OSM_MCP
    assert mcp_server_for("web_search") == RENTROLL_MCP
    assert mcp_server_for("publish_memo") == RENTROLL_MCP


def _assert_real_params(fn, *expected: str, forbidden=("args", "kwargs")) -> None:
    names = list(inspect.signature(fn).parameters)
    for key in forbidden:
        assert key not in names, names
    for key in expected:
        assert key in names, names


def test_mcp_impls_use_real_tool_schemas_not_args_kwargs():
    from cre_rentroll.mcp_runtime import book_mcp_impls

    impls = book_mcp_impls()
    _assert_real_params(impls["create_plan"], "tasks")
    _assert_real_params(impls["update_plan"], "tasks")
    _assert_real_params(impls["update_plan_status"], "tasks")
    _assert_real_params(impls["query_units"], "occupancy", "vacant_only")
    _assert_real_params(impls["compute_income"], "vacant_fill_rent")
    create_sig = inspect.signature(impls["create_plan"])
    assert "tasks" in create_sig.parameters
    assert create_sig.parameters["tasks"].default is create_sig.parameters["tasks"].empty
    qu = inspect.signature(impls["query_units"])
    assert qu.parameters["vacant_only"].default is False
    assert qu.parameters["occupancy"].default is None
    income = inspect.signature(impls["compute_income"])
    assert "vacant_fill_rent" in income.parameters
    assert income.parameters["vacant_fill_rent"].default is None


def test_strands_book_tools_schema_not_args_kwargs():
    pytest.importorskip("strands")
    from cre_rentroll.agents import _strands_tools

    by_name = {}
    for tool in _strands_tools(("create_plan", "query_units", "compute_income")):
        spec = getattr(tool, "tool_spec", None)
        assert spec, tool
        by_name[spec["name"]] = spec["inputSchema"]["json"]

    create_props = by_name["create_plan"]["properties"]
    assert "tasks" in create_props
    assert "args" not in create_props and "kwargs" not in create_props
    assert "args" not in (by_name["create_plan"].get("required") or [])
    assert "kwargs" not in (by_name["create_plan"].get("required") or [])
    assert "tasks" in (by_name["create_plan"].get("required") or [])

    qu = by_name["query_units"]
    qprops = qu.get("properties") or {}
    assert "vacant_only" in qprops
    assert "args" not in qprops and "kwargs" not in qprops
    qreq = qu.get("required") or []
    assert "args" not in qreq and "kwargs" not in qreq
    assert "vacant_only" not in qreq

    income = by_name["compute_income"]
    iprops = income.get("properties") or {}
    assert "args" not in iprops and "kwargs" not in iprops
    ireq = income.get("required") or []
    assert "args" not in ireq and "kwargs" not in ireq
    assert "vacant_fill_rent" not in ireq


def test_mcp_create_plan_once_runs_and_stores_checklist():
    from cre_rentroll.mcp_runtime import book_mcp_impls
    from cre_rentroll.run_events import bind, reset

    impls = book_mcp_impls()
    reset_turn("tester")
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        first = impls["create_plan"]("1. [in_progress] query_units\n2. [pending] compute_income")
        second = impls["create_plan"]("1. do something else")
    finally:
        reset(token)
    assert "query_units" in first
    assert "already exists" in second.lower()
    assert "update_plan" in second
    assert [it["text"] for it in checklist()] == ["query_units", "compute_income"]
    msgs = [row["message"] for row in seen]
    assert any("create_plan (MCP)" in (m or "") for m in msgs)
    assert any(row["step"] == "tool" for row in seen)
    assert any(row["step"] == "plan" for row in seen)


def test_parse_task_items_splits_comma_joined_work_tools():
    items = parse_task_items("osm_geocode (MCP), query_units (MCP)")
    assert [it["text"] for it in items] == ["osm_geocode", "query_units"]
    reset_turn("tester")
    create_plan("osm_geocode (MCP), query_units (MCP)")
    assert [it["text"] for it in checklist()] == ["osm_geocode", "query_units"]
    out = update_plan_status("osm_geocode (MCP) [completed], query_units (MCP) [in_progress]")
    assert "plan updated" in out.lower() or "completed" in out.lower()
    assert [it["status"] for it in checklist()] == ["completed", "in_progress"]
    assert "[completed] osm_geocode" in out
    assert "[in_progress] query_units" in out


def test_update_plan_status_infers_completed_when_untagged_after_work():
    reset_turn("tester")
    create_plan("1. [pending] compute_income")
    record("compute_income", {}, {"ok": True, "in_place_monthly": 80019})
    out = update_plan_status("1. compute_income (MCP)")
    assert "plan updated" in out.lower()
    assert "checklist updated" in out.lower()
    assert checklist()[0]["status"] == "completed"
    assert "[completed] compute_income" in out
    again = update_plan_status("1. compute_income (MCP)")
    assert "rejected consecutive" in again.lower()
    assert "next is score_gcr" in again.lower()
    assert "do not loop" in again.lower()
    assert "checklist updated" not in again.lower()
    assert "plan unchanged" in again.lower()
    assert checklist()[0]["status"] == "completed"


def test_update_plan_status_infers_next_in_progress_for_untagged_list():
    reset_turn("tester")
    create_plan("1. [in_progress] query_units\n2. [pending] compute_income")
    record("query_units", {}, {"ok": True, "unit_count": 164})
    out = update_plan_status("1. query_units (MCP), 2. compute_income (MCP)")
    assert [it["status"] for it in checklist()] == ["completed", "in_progress"]
    assert "[completed] query_units" in out
    assert "[in_progress] compute_income" in out


def test_parse_task_items_splits_numbered_items_on_one_line():
    blob = (
        "1. [pending] Summarize unit count and occupancy. "
        "2. Compute in-place monthly income. "
        "3. Query vacant count. "
        "4. Query month-to-month status."
    )
    items = parse_task_items(blob)
    assert [it["text"] for it in items] == [
        "Summarize unit count and occupancy.",
        "Compute in-place monthly income.",
        "Query vacant count.",
        "Query month-to-month status.",
    ]
    assert items[0]["status"] == "pending"
    assert all(it["status"] == "pending" for it in items[1:])


def test_update_plan_matches_after_one_line_numbered_create():
    reset_turn("tester")
    create_plan(
        "1. [in_progress] Summarize unit count and occupancy. "
        "2. Compute in-place monthly income. "
        "3. Query vacant count. "
        "4. Query month-to-month status."
    )
    assert [it["text"] for it in checklist()] == [
        "Summarize unit count and occupancy.",
        "Compute in-place monthly income.",
        "Query vacant count.",
        "Query month-to-month status.",
    ]
    out = update_plan_status(
        "1. [completed] Summarize unit count and occupancy.\n"
        "2. [in_progress] Compute in-place monthly income."
    )
    assert "plan updated" in out.lower()
    texts = [it["text"] for it in checklist()]
    assert texts == [
        "Summarize unit count and occupancy.",
        "Compute in-place monthly income.",
        "Query vacant count.",
        "Query month-to-month status.",
    ]
    assert [it["status"] for it in checklist()][:2] == [
        "completed",
        "in_progress",
    ]
    record("query_units", {}, {"ok": True})
    noop = update_plan_status(
        "1. [completed] Summarize unit count and occupancy.\n"
        "2. [in_progress] Compute in-place monthly income."
    )
    assert "unchanged" in noop.lower()
    assert "checklist updated" not in noop.lower()


def test_parse_task_items_completed_and_in_progress_aliases():
    items = parse_task_items(
        "1. [x] query_units\n"
        "2. [completed] compute_income\n"
        "3. compute_noi — done\n"
        "4. **in_progress** score_gcr\n"
        "5. [ ] publish_memo"
    )
    assert [it["status"] for it in items] == [
        "completed",
        "completed",
        "completed",
        "in_progress",
        "pending",
    ]
    assert items[0]["text"] == "query_units"
    assert items[2]["text"] == "compute_noi"
    assert items[3]["text"] == "score_gcr"
    jsonish = parse_task_items(
        [
            {"text": "query_units", "status": "done"},
            {"title": "[X] compute_income"},
            "- [completed] reflection self-check",
        ]
    )
    assert jsonish[0]["status"] == "completed"
    assert jsonish[1]["status"] == "completed" and jsonish[1]["text"] == "compute_income"
    assert jsonish[2]["status"] == "completed"


def test_parse_task_items_incomplete_aliases():
    items = parse_task_items(
        "1. [incomplete] compute_noi\n"
        "2. [incomplet] osm_geocode\n"
        "3. web_search — blocked\n"
        "4. [n/a] compute_cap_rate\n"
        "5. [unanswerable] publish_memo"
    )
    assert [it["status"] for it in items] == [
        "incomplete",
        "incomplete",
        "incomplete",
        "incomplete",
        "incomplete",
    ]
    assert items[1]["text"] == "osm_geocode"


def test_incomplete_is_terminal_like_completed():
    reset_turn("tester")
    mixed = [
        {"text": "query_units", "status": "completed"},
        {"text": "compute_noi", "status": "incomplete"},
        {"text": "osm_geocode", "status": "incomplete"},
    ]
    create_plan("1. [in_progress] query_units\n2. [pending] compute_noi")
    record("query_units", {}, {"ok": True, "unit_count": 10})
    out = update_plan_status(
        "1. [completed] query_units\n"
        "2. [incomplete] compute_noi"
    )
    assert [it["status"] for it in checklist()] == ["completed", "incomplete"]
    assert "incomplete: compute_noi" in out
    assert "2. [incomplete] compute_noi" in out
    log = traces()[-1]["result"]["log"]
    assert "incomplete: compute_noi" in log


def test_create_plan_stores_plan():
    reset_turn("tester")
    out = create_plan("1. query_units\n2. compute_income")
    assert "query_units" in out
    assert llm_kind_ran("create_plan")
    assert "compute_income" in (llm_plan() or "")
    names = [t["name"] for t in traces()]
    assert "create_plan" in names
    stored = traces()[-1]["result"]["plan"]
    assert "query_units" in stored
    assert "1. [pending] query_units" in stored
    listed = create_plan(["query_units", "compute_income"])
    assert "already exists" in listed.lower()
    assert "update_plan" in listed
    assert [it["text"] for it in checklist()] == ["query_units", "compute_income"]


def test_reflection_records_analysis_and_need_replan():
    reset_turn("tester")
    stored = reflection("occupancy looks complete; income still missing")
    assert "income still missing" in stored
    assert llm_kind_ran("reflection")
    row = traces()[-1]
    assert row["name"] == "reflection"
    assert row["args"]["analysis"].startswith("occupancy")
    assert row["result"]["need_replan"] is False

    reset_turn("tester")
    seen = []
    token = bind(seen.append)
    try:
        msg = reflection("checklist missed compute_income", need_replan=True)
    finally:
        reset(token)
    assert "update_plan" in msg
    assert "new remaining work" in msg.lower() or "completed/incomplete rows stay" in msg.lower()
    row = traces()[-1]
    assert row["result"]["need_replan"] is True
    steps = [r["step"] for r in seen]
    assert "tool" in steps and "reflect" in steps and "replan" in steps
    assert any("checklist missed compute_income" in (r.get("message") or "") for r in seen)
    assert any("reflection (MCP)" in (r.get("message") or "") for r in seen)
    assert not any("skip decompose" in (r.get("message") or "") for r in seen)


def test_tool_message_uses_model_plan_and_analysis():
    plan = tool_message(
        "create_plan",
        {"tasks": "1. query_units 2. compute_income"},
        {"plan": "1. query_units 2. compute_income", "ok": True},
    )
    assert "create_plan (MCP)" in plan
    assert "query_units" in plan
    ref = tool_message(
        "reflection",
        {"analysis": "self-check before RollAbstract", "need_replan": False},
        {"analysis": "self-check before RollAbstract", "need_replan": False},
    )
    assert "reflection (MCP)" in ref
    assert "self-check" in ref
    labeled = annotate_message(
        "create_plan then update_plan then update_plan_status then reflection"
    )
    assert labeled == (
        "create_plan (MCP) then update_plan (MCP) then update_plan_status (MCP) "
        "then reflection (MCP)"
    )


def test_harness_empty_draft_one_run_agent_not_inject_compute_income(monkeypatch):
    roll = _roll()
    seen: list[dict] = []
    token = bind(seen.append)
    calls: list[str] = []

    def fake_agent(prompt, *_a, **_k):
        calls.append(prompt)
        create_plan("1. query_units\n2. compute_income")
        reflection("empty draft; still need occupancy and income")
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

    assert len(calls) == 1
    msgs = [row["message"] for row in seen]
    assert any("create_plan (MCP)" in m and "query_units" in m for m in msgs)
    assert any("empty draft; still need occupancy" in m for m in msgs)
    assert not any("skip decompose" in m for m in msgs)
    assert not any("second ReAct pass" in m for m in msgs)
    assert not any("harness injection" in m.lower() for m in msgs)
    assert not any(
        row["step"] == "tool" and (row.get("data") or {}).get("tool") == "compute_income"
        for row in seen
    )
    eval_msgs = [row["message"] for row in seen if row["step"] == "eval"]
    assert any("judge=not_run" in m and "→ error" in m for m in eval_msgs)
    assert any("G=N/A C=N/A R=N/A vault_calc=N/A" in m for m in eval_msgs)
    assert not any("G=5" in m and "judge=llm" in m for m in eval_msgs)
    assert not any("G=1" in m for m in eval_msgs)
    drafts = [row["message"] for row in seen if row["step"] == "draft"]
    assert any("Draft before eval is empty" in m for m in drafts)
    plan_i = next(i for i, row in enumerate(seen) if row["step"] == "plan")
    reflect_i = next(i for i, row in enumerate(seen) if row["step"] == "reflect")
    assert plan_i < reflect_i


def test_harness_inventory_emit_is_not_step_plan(monkeypatch):
    roll = _roll()
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
    inv_msgs = [row["message"] for row in seen if row["step"] == "inventory"]
    plan_rows = [row for row in seen if row["step"] == "plan"]
    skill_rows = [row for row in seen if row["step"] == "skills"]
    assert any("full tool menu" in m and "book" in m for m in inv_msgs)
    assert not any("RollAbstractor" in m for m in inv_msgs)
    assert not any("(LLM)" in m for m in inv_msgs)
    assert len(skill_rows) == 1
    assert skill_rows[0]["message"] == (
        "attached rent-roll-abstract, citation-policy, underwriting"
    )
    assert "(LLM)" not in skill_rows[0]["message"]
    assert not any("used skill" in (row.get("message") or "").lower() for row in seen)
    assert not any("skip decompose" in (row.get("message") or "") for row in seen)
    assert not any("cite rent-roll report date" in (row.get("message") or "") for row in seen)
    assert not any("RollAbstractor" in (row.get("message") or "") for row in plan_rows)
    assert not any("second ReAct pass" in (row.get("message") or "") for row in seen)
    assert not any("harness injection" in (row.get("message") or "").lower() for row in seen)
    eval_msgs = [row["message"] for row in seen if row["step"] == "eval"]
    assert any("judge=not_run" in m and "→ error" in m for m in eval_msgs)
    assert any("vault_calc=N/A" in m for m in eval_msgs)
    assert not any("G=5" in m and "judge=llm" in m for m in eval_msgs)
    assert not any("G=1" in m for m in eval_msgs)


def test_create_plan_then_update_plan_logs_full_list_then_change():
    reset_turn("tester")
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        create_plan(
            "1. [in_progress] query_units\n2. [pending] compute_income"
        )
        update_plan_status(
            "1. [completed] query_units\n2. [in_progress] compute_income"
        )
    finally:
        reset(token)
    plans = [row["message"] for row in seen if row["step"] == "plan"]
    assert len(plans) == 2
    first, second = plans
    assert "initial checklist" in first
    assert "(LLM)" in first
    assert "create_plan (MCP)" in first
    assert "[in_progress] query_units" in first
    assert "[pending] compute_income" in first
    assert "checklist updated" in second
    assert "(LLM)" in second
    assert "update_plan_status (MCP)" in second
    assert "completed: query_units" in second
    assert "in_progress: compute_income" in second
    assert "[completed] query_units" in second
    assert "[in_progress] compute_income" in second
    assert "reflection self-check" not in second


def test_update_plan_keeps_original_when_replacement_one_liner():
    reset_turn("tester")
    create_plan("1. [in_progress] query_units\n2. [pending] compute_income")
    out = update_plan_status("1. [completed] draft the answer")
    assert "kept original" in out.lower() or "did not match" in out.lower()
    texts = [it["text"] for it in checklist()]
    assert texts == ["query_units", "compute_income"]
    assert checklist()[0]["status"] == "in_progress"
    assert checklist()[1]["status"] == "pending"
    record("query_units", {}, {"ok": True})
    extra = update_plan_status(
        "1. [completed] query_units\n2. [in_progress] compute_income\n3. [pending] extra work"
    )
    assert "extra work" not in extra
    assert [it["text"] for it in checklist()] == ["query_units", "compute_income"]
    assert checklist()[0]["status"] == "completed"
    assert checklist()[1]["status"] == "in_progress"


def test_update_plan_replaces_checklist_on_replan():
    reset_turn("tester")
    create_plan(
        "1. [in_progress] Web search recent multifamily cap rate for this property's metro."
    )
    replaced = update_plan(
        "1. [in_progress] web_search current multifamily cap rates San Francisco Bay Area\n"
        "2. [pending] score_gcr"
    )
    assert "plan replaced" in replaced.lower()
    texts = [it["text"] for it in checklist()]
    assert texts[0].startswith("web_search current multifamily")
    assert checklist()[0]["status"] == "in_progress"
    assert "property's metro" not in texts[0]


def test_update_plan_keeps_completed_tasks_on_replan():
    reset_turn("tester")
    create_plan(
        "1. [in_progress] web_search property metro\n"
        "2. [pending] draft answer"
    )
    update_plan_status(
        "1. [completed] web_search property metro\n"
        "2. [in_progress] draft answer"
    )
    replaced = update_plan(
        "1. [in_progress] web_search San Francisco Bay Area cap rates"
    )
    assert "kept completed" in replaced.lower()
    items = checklist()
    assert items[0]["text"] == "web_search property metro"
    assert items[0]["status"] == "completed"
    assert "Bay Area" in items[1]["text"]
    assert items[1]["status"] == "in_progress"
    assert len(items) == 2


def test_vacant_only_prompt_does_not_require_compute_income(monkeypatch):
    from cre_rentroll.agents import system_prompt
    from cre_rentroll.tools import reflection as reflection_fn

    sys = system_prompt(BOOK, "").lower()
    assert "first action" in sys
    assert "always call create_plan" in sys
    assert "never skip create_plan" in sys
    assert "skip create_plan only" not in sys
    assert "create_plan" in sys
    assert "update_plan" in sys
    assert "update_plan_status" in sys
    assert "same items" in sys
    assert "not replace the list with a new one-liner" in sys or "new one-liner" in sys
    assert "new remaining work" in sys
    assert "stay on the list" in sys or "kept automatically" in sys or "automatically" in sys
    assert "[pending]" in sys or "pending" in sys
    assert "vacant-only" in sys
    assert "do not require compute_income" in sys
    assert "do not always call compute_income" in sys
    assert "mtm_count" in sys
    assert "avg_sqft" in sys
    assert "does not specify square footage" in sys
    assert "hand-average" in sys
    assert "short bullet per asked item" in sys
    assert "not one paragraph" in sys
    assert "units on month-to-month leases" in sys
    assert "existence/yes-no" in sys
    assert "occupancy status" in sys
    assert "occupied/vacant/all" in sys
    assert "one unfiltered query_units" in sys
    assert "not four" in sys
    assert "do not call query_units again for those counts" in sys
    assert "occupancy=mtm" not in sys
    assert "query_units-for-mtm" not in sys
    assert "any month-to-month" not in sys
    assert "any month-to-month on this rent roll" not in sys
    assert "mark that step" in sys and "[completed]" in sys
    assert "next" in sys and "[in_progress]" in sys
    assert "do not resend every line as [pending]" in sys
    assert "after update_plan_status" in sys
    assert "must be that work tool" in sys
    assert "forbidden: update_plan_status" in sys
    assert "[incomplete]" in sys
    assert "do not retry that item" in sys
    assert "score_gcr" in sys
    assert "text-only" in sys
    assert "score_gcr(draft)" in sys
    assert "do not stop with a sentence and no score_gcr" in sys
    assert "classify_scope" not in sys
    assert "first job" not in sys
    assert "answer without tools" in sys
    assert "identity" in sys
    assert "agent's name" in sys
    assert "chitchat" in sys
    assert "what's your name" not in sys
    assert "who are you" not in sys
    assert "where is this property" in sys
    assert "one osm_geocode" in sys
    assert "must be create_plan items" in sys
    assert "never call a work tool that is not on the checklist" in sys
    assert "one work tool per numbered line" in sys
    assert "per needed work tool" in sys
    assert "per asked workstream" not in sys
    assert "not one item per comma" in sys or "not per comma" in sys
    assert "one-item checklist is fine when that one tool" in sys
    assert "compute_cap_rate" in sys
    assert "add the missing items" in sys
    assert "do not pad with abstract" in sys
    assert "never the out-of-scope refuse" in sys
    assert "including locate/map" in sys
    has = system_prompt(BOOK, "VAULT HAS a rent roll for this session.\n").lower()
    assert "please attach a rent-roll csv" not in has
    assert "already ingested" in has
    assert "spreadsheet filename" in has or "rent roll" in has
    assert "buy" in has and "worth" in has
    assert "in-scope underwriting" in has
    assert "never emit" in has and "out-of-scope refuse" in has
    assert "answers questions about the property, the deal, or the area it sits in" in has
    assert "personal chat" in has
    assert "[incomplete]" in has
    assert "do not refuse" in has
    assert "never copy (skill)" in has
    assert "idle weekend weather" in has
    assert "flood" not in has
    assert "population" not in has
    empty = system_prompt(BOOK, "VAULT EMPTY: no rent roll is ingested.\n").lower()
    assert "please attach a rent-roll csv" in empty
    assert "identity" in empty
    assert "out-of-scope refuse" in empty or "need-file" in empty
    assert "average property value" in empty
    assert "never that need-file sentence" in empty or (
        "never" in empty and "need-file" in empty and "web_search" in empty
    )
    assert "do not require rollabstract json" in sys
    assert "need_replan until turn 12" in sys
    create_doc = " ".join((create_plan.__doc__ or "").lower().split())
    assert "identity" in create_doc
    assert "chitchat" in create_doc
    assert "every in-scope" in create_doc or "never skip" in create_doc
    assert "once" in create_doc
    assert "do not call again" in create_doc or "update_plan" in create_doc
    assert "per needed work tool" in create_doc
    assert "per asked workstream" not in create_doc
    assert "one-item checklist is fine when that one tool" in create_doc
    assert "update_plan with" in create_doc and "missing items" in create_doc
    assert "mtm_count" in create_doc
    assert "existence/yes-no" in create_doc
    assert "one unfiltered query_units" in create_doc
    assert "not four" in create_doc
    assert "do not call query_units again for those counts" in create_doc
    assert "query_units-for-mtm" not in create_doc
    assert "any month-to-month on this rent roll" not in create_doc
    qu_doc = (query_units.__doc__ or "").lower()
    assert "occupied" in qu_doc and "vacant" in qu_doc and "all" in qu_doc
    assert "occupancy status" in qu_doc
    assert "occupancy=mtm" not in qu_doc
    assert "occupied_count" in qu_doc and "vacant_count" in qu_doc
    assert "occupancy_pct" in qu_doc and "unit_count" in qu_doc
    assert "mtm_count" in qu_doc
    assert "avg_sqft" in qu_doc
    assert "does not specify square footage" in qu_doc
    assert "hand-average" in qu_doc
    assert "one unfiltered query_units" in qu_doc
    assert "do not call twice" in qu_doc
    assert "never call occupancy=occupied" not in qu_doc
    assert "any month-to-month on this rent roll" not in qu_doc
    inc_doc = (compute_income.__doc__ or "").lower()
    assert "mtm_count" in inc_doc
    assert "occupied_count" in inc_doc and "vacant_count" in inc_doc
    assert "occupancy_pct" in inc_doc
    assert "units on month-to-month leases" in inc_doc
    assert "existence/yes-no" in inc_doc
    assert "any mtm" not in inc_doc
    assert "estimated_full_occupancy_monthly" in inc_doc
    assert "mean_occupied_rent" in inc_doc
    assert "100% occupancy" in inc_doc or "all-units" in inc_doc
    assert "do not repeat in_place_monthly" in inc_doc
    assert "vacant_fill_rent" in inc_doc
    assert "scenario_full_occupancy_annual" in inc_doc
    assert "if all units were occupied" in sys or "100% occupancy" in sys
    assert "estimated_full_occupancy_monthly" in sys
    assert "scenario_full_occupancy_annual" in sys
    assert "vacant_fill_rent" in sys
    assert "do not answer a different question" in sys
    assert "same as with vacancy" in sys
    update_doc = (update_plan.__doc__ or "").lower()
    assert "replace" in update_doc
    assert "update_plan_status" in update_doc
    status_doc = (update_plan_status.__doc__ or "").lower()
    assert "same items" in status_doc or "does not replace" in status_doc
    assert "not replace" in status_doc or "one-liner" in status_doc
    assert "after each vault tool" in status_doc or "immediately after a real vault" in status_doc
    assert "twice in a row" in status_doc or "not update_plan_status" in status_doc
    assert "[incomplete]" in status_doc
    assert "incomplet" in status_doc
    assert "do not retry" in status_doc
    doc = (reflection_fn.__doc__ or "").lower()
    assert "not after every tool" in doc or "once as a progress" in doc
    assert "self-check" in doc
    assert "vacant-only does not require compute_income" in doc

    roll = _roll()
    prompts: list[str] = []

    def fake_agent(prompt, *_a, **_k):
        prompts.append(prompt)
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
    run_task("tester", "How many vacant units?", skip_scope=True)
    assert len(prompts) == 1
    joined = " ".join(prompts).lower()
    assert "always call compute_income" not in joined
    assert "second react" not in joined


def test_agents_openai_specs_include_react_tools():
    from cre_rentroll.agents import INVENTORIES, _openai_specs

    names = BOOK.tool_names
    spec = _openai_specs(names)
    fnames = [s["function"]["name"] for s in spec]
    create_desc = next(
        s["function"]["parameters"]["properties"]["tasks"]["description"]
        for s in spec
        if s["function"]["name"] == "create_plan"
    ).lower()
    assert "per needed work tool" in create_desc
    assert "not per comma" in create_desc
    assert "per asked workstream" not in create_desc
    assert "one-item list is fine when that one tool" in create_desc
    assert "create_plan" in fnames
    assert "update_plan" in fnames
    assert "update_plan_status" in fnames
    assert "reflection" in fnames
    assert "score_gcr" in fnames
    assert "compute_income" in fnames


def test_update_plan_applies_statuses_without_next_hints():
    reset_turn("tester")
    create_plan("1. [in_progress] query_units\n2. [pending] compute_income")
    out = update_plan_status("1. [completed] query_units\n2. [in_progress] compute_income")
    assert "Plan updated:" in out
    assert "Next:" not in out
    record("compute_income", {}, {"ok": True})
    done = update_plan_status("1. [completed] query_units\n2. [completed] compute_income")
    assert "Next:" not in done
    assert [it["status"] for it in checklist()] == ["completed", "completed"]


def test_consecutive_update_plan_applies_without_vault_tool():
    from cre_rentroll.turn_ctx import last_tool_name

    reset_turn("tester")
    create_plan("1. [in_progress] query_units\n2. [pending] compute_income")
    first = update_plan_status("1. [completed] query_units\n2. [in_progress] compute_income")
    assert "Plan updated:" in first
    assert last_tool_name() == "update_plan_status"
    second = update_plan_status("1. [completed] query_units\n2. [completed] compute_income")
    assert "rejected consecutive" in second.lower()
    assert "do not loop" in second.lower()
    assert "next is compute_income" in second.lower()
    assert "checklist updated" not in second.lower()
    assert checklist()[1]["status"] == "in_progress"
    reflection("statuses look right")
    third = update_plan_status("1. [completed] query_units\n2. [completed] compute_income")
    assert "rejected consecutive" in third.lower()
    assert checklist()[1]["status"] == "in_progress"


def test_second_update_plan_completes_compute_income_after_trace():
    reset_turn("tester")
    create_plan("1. [in_progress] query_units\n2. [pending] compute_income")
    record("query_units", {}, {"ok": True, "unit_count": 164})
    first = update_plan_status("1. [completed] query_units\n2. [in_progress] compute_income")
    assert "Plan updated:" in first
    assert checklist()[1]["status"] == "in_progress"
    record("compute_income", {}, {"ok": True, "in_place_monthly": 80019})
    second = update_plan_status("1. [completed] query_units\n2. [completed] compute_income")
    assert "Plan updated:" in second
    assert [it["status"] for it in checklist()] == ["completed", "completed"]


def test_consecutive_update_after_all_vault_tools_is_status_only():
    reset_turn("tester")
    create_plan("1. [in_progress] query_units\n2. [pending] compute_income")
    record("query_units", {}, {"ok": True})
    record("compute_income", {}, {"ok": True})
    done = update_plan_status("1. [completed] query_units\n2. [completed] compute_income")
    assert [it["status"] for it in checklist()] == ["completed", "completed"]
    again = update_plan_status("1. [completed] query_units\n2. [completed] compute_income")
    assert "rejected consecutive" in again.lower()
    assert "next is score_gcr" in again.lower()
    assert "do not loop" in again.lower()
    assert "checklist updated" not in again.lower()
    assert [it["status"] for it in checklist()] == ["completed", "completed"]


def test_second_update_completes_in_place_monthly_income_phrase_after_traces():
    reset_turn("tester")
    create_plan(
        "1. [in_progress] query_units\n2. [pending] Compute in-place monthly income"
    )
    record("query_units", {}, {"ok": True, "unit_count": 164, "occupied_count": 151})
    first = update_plan_status(
        "1. [completed] query_units\n"
        "2. [pending] Compute in-place monthly income"
    )
    assert checklist()[1]["status"] == "pending"
    record("compute_income", {}, {"ok": True, "in_place_monthly": 80019})
    second = update_plan_status(
        "1. [completed] query_units\n"
        "2. [completed] Compute in-place monthly income"
    )
    assert [it["status"] for it in checklist()] == ["completed", "completed"]
    log = traces()[-1]["result"]["log"]
    assert "completed: Compute in-place monthly income" in log


def test_react_turn_task_suffix_maps_phrase_like_update_plan_status():
    reset_turn("tester")
    items = [
        {"text": "query_units", "status": "completed"},
        {"text": "Compute income for in-place monthly rent.", "status": "pending"},
    ]
    assert react_turn_task_suffix(["compute_income"], items) == (
        "for 2. [pending] Compute income for in-place monthly rent."
    )
    assert react_turn_task_suffix(["query_units"], items) == "for (no matching task)"
    items[0]["status"] = "in_progress"
    assert react_turn_task_suffix(["query_units"], items) == "for 1. [in_progress] query_units"
    assert react_turn_task_suffix(["score_gcr"], items) == ""
    assert react_turn_task_suffix(["create_plan"], items) == ""
    assert react_turn_task_suffix(["reflection"], items) == ""
    assert react_turn_task_suffix(["compute_income"], []) == ""
    assert react_turn_task_suffix(["compute_income"], None) == ""


def test_vault_tools_post_to_deal_mcp_and_have_no_local_math(monkeypatch):
    import inspect

    from cre_rentroll.capabilities_client import GATEWAY_REQUIRED

    reset_turn("ada")
    monkeypatch.delenv("CAPABILITIES_GATEWAY_URL", raising=False)
    missing = json.loads(query_units())
    assert missing["ok"] is False
    assert "capabilities gateway is required" in missing["error"]
    assert missing["error"] == GATEWAY_REQUIRED

    monkeypatch.setenv("CAPABILITIES_GATEWAY_URL", "http://127.0.0.1:8010")
    seen: list[dict] = []

    def fake(name, arguments, timeout=180.0):
        seen.append({"name": name, "arguments": arguments})
        if name == "query_units":
            return json.dumps({"unit_count": 164, "ok": True})
        return json.dumps({"in_place_monthly": 120800, "ok": True})

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)
    assert json.loads(query_units(vacant_only=True))["unit_count"] == 164
    assert json.loads(compute_income(vacant_fill_rent=5000))["in_place_monthly"] == 120800
    assert seen[0]["name"] == "query_units"
    assert seen[0]["arguments"]["vacant_only"] is True
    assert seen[0]["arguments"]["username"] == "ada"
    assert seen[1]["name"] == "compute_income"
    assert seen[1]["arguments"]["vacant_fill_rent"] == 5000
    def _code(fn) -> str:
        src = inspect.getsource(fn)
        if '"""' in src:
            _, _, rest = src.partition('"""')
            _, _, rest = rest.partition('"""')
            return rest
        return src

    code = _code(query_units) + _code(compute_income)
    assert code.count("return _capability(") == 2
    assert "load_roll" not in code
    assert "in_place_monthly" not in code
    assert "nominatim" not in code.lower()


def test_avg_sqft_skill_and_gold_case():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    gateway = root.parent / "capabilities-gateway" / "skills"
    abstract = (gateway / "rent-roll-abstract" / "SKILL.md").read_text(encoding="utf-8")
    cite = (gateway / "citation-policy" / "SKILL.md").read_text(encoding="utf-8")
    gold = json.loads((root / "tests" / "gold.json").read_text(encoding="utf-8"))
    assert "avg_sqft" in abstract
    assert "does not specify square footage" in abstract
    assert "hand-average" in abstract
    assert "short bullet per asked item" in abstract
    assert "avg_sqft" in cite
    case = next(c for c in gold["cases"] if c["id"] == "avg-sqft")
    assert case["query"] == "whats the average sqft?"
    assert "query_units" in case["required_tools"]


def test_create_plan_three_needed_tools_are_three_items():
    reset_turn("tester")
    create_plan(
        "1. [in_progress] osm_geocode\n"
        "2. [pending] query_units\n"
        "3. [pending] compute_cap_rate"
    )
    assert [it["text"] for it in checklist()] == [
        "osm_geocode",
        "query_units",
        "compute_cap_rate",
    ]
    assert len(checklist()) == 3


def test_create_plan_keeps_headline_count_items_as_written():
    reset_turn("tester")
    create_plan(
        "1. [in_progress] query_units\n"
        "2. [pending] occupancy\n"
        "3. [pending] vacant\n"
        "4. [pending] mtm_count\n"
        "5. [pending] compute_income"
    )
    assert [it["text"] for it in checklist()] == [
        "query_units",
        "occupancy",
        "vacant",
        "mtm_count",
        "compute_income",
    ]
    assert checklist()[0]["status"] == "in_progress"


def test_session_prompt_stuffs_prior_user_and_assistant_turns():
    text = session_prompt(
        "but this is the same as with 15 vacant",
        [
            {"role": "user", "content": "unit count occupancy in-place vacant MTM"},
            {
                "role": "assistant",
                "content": "250 units, 94% occupied, in-place monthly $169,906.85, 15 vacant, 48 MTM.",
            },
            {"role": "user", "content": "does in-place include vacant?"},
            {"role": "assistant", "content": "No — occupied only."},
        ],
    )
    assert "User: unit count occupancy in-place vacant MTM" in text
    assert "169,906.85" in text
    assert "does in-place include vacant?" in text
    assert "occupied only" in text
    assert "Answer the last User message" in text
    assert "first tool is always create_plan" in text.lower()
    assert "per needed work tool" in text.lower()
    assert "not per comma" in text.lower()
    assert "per asked workstream" not in text.lower()
    assert "one-item list is fine when that one tool" in text.lower()
    assert "update_plan with the missing items" in text.lower()
    assert "identity" in text.lower()
    assert "chitchat" in text.lower()
    assert "what's your name" not in text.lower()
    assert "how are you" not in text.lower()
    assert "User: but this is the same as with 15 vacant" in text
    assert text.rfind("User: but this is the same as with 15 vacant") > text.rfind(
        "User: does in-place include vacant?"
    )


def test_rent_roll_definition_prompt_does_not_always_plan_multi3():
    from cre_rentroll.agents import system_prompt, _openai_specs
    from cre_rentroll.scope import in_scope

    q = "What's a rent roll?"
    assert in_scope(q)
    sys = system_prompt(BOOK, "VAULT HAS a rent roll for this session.\n").lower()
    create_doc = " ".join((create_plan.__doc__ or "").lower().split())
    create_desc = next(
        s["function"]["parameters"]["properties"]["tasks"]["description"]
        for s in _openai_specs(BOOK.tool_names)
        if s["function"]["name"] == "create_plan"
    ).lower()
    history = []
    for i in range(11):
        history.append(
            {
                "role": "user",
                "content": (
                    "Put this property on a map, how many units are month-to-month, "
                    f"and what cap rate is implied at a $12M ask? turn {i}"
                ),
            }
        )
        history.append(
            {
                "role": "assistant",
                "content": f"Mapped, 48 MTM, implied cap 8.0%. prior {i}",
            }
        )
    follow = session_prompt(q, history).lower()
    for blob in (sys, create_doc, create_desc, follow):
        assert "osm_geocode" in blob and "query_units" in blob and "compute_cap_rate" in blob
        assert "only if this last message" in blob or "if this last message needs map" in blob
        assert "never copy that three-item list" in blob
        assert "map+mtm+cap needs osm_geocode" not in blob
        assert "definition of a rent roll" in blob
        assert "web_search" in blob
    assert "do not re-run earlier" in follow
    assert "what's a rent roll?" in follow
    assert follow.rfind("user: what's a rent roll?") > follow.rfind("user: put this property")


def test_area_property_value_prompt_is_web_search_not_need_file(monkeypatch):
    from cre_rentroll.agents import _openai_specs, system_prompt
    from cre_rentroll.harness import _facts_for_agent
    from cre_rentroll.scope import NEED_FILE_TEXT
    from cre_rentroll.tools import create_plan as create_plan_fn

    q = "What's the average property value in that area?"
    empty = system_prompt(BOOK, "VAULT EMPTY: no rent roll is ingested.\n")
    has = system_prompt(BOOK, "VAULT HAS a rent roll for this session.\n")
    follow = session_prompt(
        q,
        [
            {"role": "user", "content": "Where is this property? Can you put it on a map?"},
            {
                "role": "assistant",
                "content": "12630 Ashford Point Drive, Alief, Houston, TX",
            },
        ],
    )
    create_doc = " ".join((create_plan_fn.__doc__ or "").split())
    create_desc = next(
        s["function"]["parameters"]["properties"]["tasks"]["description"]
        for s in _openai_specs(BOOK.tool_names)
        if s["function"]["name"] == "create_plan"
    )
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    facts = _facts_for_agent("tester", False)

    for blob in (empty, has, follow, create_doc, create_desc, facts):
        low = blob.lower()
        assert "web_search" in low
        assert "average property value" in low
        assert "metro" in low or "comps" in low or "that area" in low

    assert NEED_FILE_TEXT not in follow
    assert NEED_FILE_TEXT not in has
    assert NEED_FILE_TEXT in empty
    assert "never that need-file sentence" in empty.lower()
    assert "do not call query_units, compute_*, osm_geocode, or web_search" not in facts.lower()
    assert "never the need-file sentence" in facts.lower()
    # Map-then-value: last street is already in the session user prompt.
    assert "12630 ashford point drive" in follow.lower()
    assert "where is this property" in follow.lower()
    skill = empty.lower()
    assert "not buy-this-roll" in skill or "is not buy-this-roll" in skill
    assert "compute_income" in skill
    # Area-value block must not instruct NEED_FILE_TEXT as the reply.
    idx = empty.lower().find("average property value")
    assert idx != -1
    area_block = empty[idx : idx + 500].lower()
    assert "web_search" in area_block
    assert NEED_FILE_TEXT.lower() not in area_block


def test_vault_has_locate_correction_never_need_file_last_message_wins(monkeypatch):
    from cre_rentroll.agents import system_prompt
    from cre_rentroll.harness import _facts_for_agent
    from cre_rentroll.scope import NEED_FILE_TEXT

    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "FACT#property_name=La Cordoba")
    has = system_prompt(BOOK, "VAULT HAS a rent roll for this session.\n")
    facts = _facts_for_agent("tester", True)
    history = [
        {
            "role": "user",
            "content": (
                "Put this property on a map, how many units are month-to-month, "
                "and what cap rate is implied at a $12M ask?"
            ),
        },
        {
            "role": "assistant",
            "content": "Mapped to La Córdoba, Michoacán, México. 12 MTM. Cap 8%.",
        },
    ]
    follow = session_prompt("It is not in Mexico it is in the USA", history)
    search = session_prompt("Search the USA map", history)
    attached = session_prompt("I already attached at the beginning", history)
    create_doc = " ".join((create_plan.__doc__ or "").lower().split())

    for blob in (has, facts, follow, search, attached):
        assert NEED_FILE_TEXT not in blob
        low = blob.lower()
        assert "last user message wins" in low or "last user" in low
        assert "need-file" in low
        assert "previous osm town" in low
        assert "ashford" not in low
        assert "77082" not in low
        assert "mexican town" not in low

    assert "already ingested" in has.lower()
    assert "never the need-file sentence" in has.lower()
    assert "property_name" in has.lower() and "unit_count" in has.lower()
    assert "web_search" in has.lower() and "osm_geocode" in has.lower()
    assert follow.lower().rfind("it is not in mexico") > follow.lower().rfind("michoacán")
    assert "search the usa map" in search.lower()
    assert "i already attached at the beginning" in attached.lower()
    assert "last user" in create_doc
    assert "previous osm town" in create_doc
    assert "mexico" not in has.lower()
    assert "united states" not in has.lower()


def test_session_prompt_keeps_current_user_when_history_is_huge():
    history = [
        {"role": "user", "content": f"old question {i} " + ("x" * 400)}
        for i in range(40)
    ]
    text = session_prompt("what if all occupied?", history)
    assert "User: what if all occupied?" in text
    assert "Answer the last User message" in text
    assert "earlier session turns omitted" in text


def test_run_task_passes_session_history_into_agent(monkeypatch):
    roll = _roll()
    prompts: list[str] = []
    history = [
        {"role": "user", "content": "What is in-place monthly?"},
        {"role": "assistant", "content": "In-place monthly is $169,906.85 (occupied only)."},
    ]

    def fake_agent(prompt, *_a, **_k):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": history})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    run_task("tester", "but this is the same as with 15 vacant", skip_scope=True)
    assert len(prompts) == 1
    blob = prompts[0]
    assert "What is in-place monthly?" in blob
    assert "169,906.85" in blob
    assert "but this is the same as with 15 vacant" in blob
    assert "Answer the last User message" in blob


def test_vault_calc_accepts_gateway_income_result():
    from cre_rentroll.eval_service import vault_calc_ok

    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=800, vacant=False, occupancy="Occupied")],
    )
    income = {
        "in_place_annual": 9600,
        "vacant_fill_annual": 60000,
        "scenario_full_occupancy_annual": 69600,
    }
    draft = (
        "If all units are occupied, the total annual income would be $69,600, "
        "including in-place $9,600 plus $60,000 from vacant fill."
    )
    assert vault_calc_ok(draft, roll, [{"name": "compute_income", "result": income}]) is not False

