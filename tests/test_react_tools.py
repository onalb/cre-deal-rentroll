import inspect
import json

import pytest

from cre_rentroll.agents import BOOK, INVENTORIES
from cre_rentroll.harness import run_task, session_prompt
from cre_rentroll.run_events import annotate_message, bind, reset, tool_message
from cre_rentroll.schemas import CanonicalRentRoll, UnitRow
from cre_rentroll.tools import (
    HEADLINE_COUNTS_NOTE,
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


def test_parse_task_items_completed_and_in_progress_aliases():
    items = parse_task_items(
        "1. [x] query_units\n"
        "2. [completed] compute_income\n"
        "3. compute_noi — done\n"
        "4. **in_progress** score_gcr\n"
        "5. [ ] web_search"
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
    assert "where is this property" in sys
    assert "one osm_geocode item" in sys
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
    assert "i only answer questions about an uploaded rent roll" in has
    assert "[incomplete]" in has
    assert "do not refuse" in has
    assert "never copy (skill)" in has
    assert "weather, sports, recipes" in has
    empty = system_prompt(BOOK, "VAULT EMPTY: no rent roll is ingested.\n").lower()
    assert "please attach a rent-roll csv" in empty
    assert "do not require rollabstract json" in sys
    assert "need_replan until turn 12" in sys
    create_doc = (create_plan.__doc__ or "").lower()
    assert "every in-scope" in create_doc or "never skip" in create_doc
    assert "once" in create_doc
    assert "do not call again" in create_doc or "update_plan" in create_doc
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
    assert "update_plan skipped" not in second
    assert checklist()[1]["status"] == "completed"
    reflection("statuses look right")
    third = update_plan_status("1. [completed] query_units\n2. [completed] compute_income")
    assert "unchanged" in third.lower()
    assert checklist()[1]["status"] == "completed"


def test_second_update_plan_completes_compute_income_after_trace():
    reset_turn("tester")
    create_plan("1. [in_progress] query_units\n2. [pending] compute_income")
    record("query_units", {}, {"ok": True, "unit_count": 164})
    record("compute_income", {}, {"ok": True, "in_place_monthly": 80019})
    first = update_plan_status("1. [completed] query_units\n2. [in_progress] compute_income")
    assert "Plan updated:" in first
    assert checklist()[1]["status"] == "in_progress"
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
    assert "update_plan skipped" not in again
    assert [it["status"] for it in checklist()] == ["completed", "completed"]


def test_second_update_completes_in_place_monthly_income_phrase_after_traces():
    reset_turn("tester")
    create_plan(
        "1. [in_progress] query_units\n2. [pending] Compute in-place monthly income"
    )
    record("query_units", {}, {"ok": True, "unit_count": 164, "occupied_count": 151})
    record("compute_income", {}, {"ok": True, "in_place_monthly": 80019})
    first = update_plan_status(
        "1. [completed] query_units\n"
        "2. [pending] Compute in-place monthly income"
    )
    assert checklist()[1]["status"] == "pending"
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


def test_query_units_payload_includes_roll_mtm_count(monkeypatch):
    reset_turn("tester")
    occupied = [
        UnitRow(unit_id=str(i), in_place_rent=800, vacant=False, occupancy="Occupied")
        for i in range(151)
    ]
    vacant = [
        UnitRow(unit_id=f"v{i}", in_place_rent=0, vacant=True, occupancy="Vacant")
        for i in range(13)
    ]
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=occupied + vacant,
    )
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    payload = json.loads(query_units())
    assert payload["unit_count"] == 164
    assert payload["occupied_count"] == 151
    assert payload["vacant_count"] == 13
    assert payload["occupancy_pct"] == round(151 / 164, 4)
    assert payload["mtm_count"] == roll.mtm_count == 0
    assert payload["note"] == HEADLINE_COUNTS_NOTE
    assert "complete in this result" in payload["note"]
    assert "list a filtered subset of unit rows" in payload["note"].lower()
    assert "not to re-fetch counts" in payload["note"]
    income = json.loads(compute_income())
    assert income["mtm_count"] == payload["mtm_count"]
    assert income["in_place_monthly"] == 151 * 800
    assert income["mean_occupied_rent"] == 800.0
    assert income["vacant_asking_monthly"] == 0.0
    assert income["estimated_full_occupancy_monthly"] == 164 * 800
    assert "occupied units only" in income["note"]
    filtered = json.loads(query_units(vacant_only=True))
    assert filtered["mtm_count"] == roll.mtm_count
    assert filtered["vacant_count"] == 13
    assert len(filtered["units"]) == 13
    assert "note" not in filtered
    occ = json.loads(query_units(occupancy="Vacant"))
    assert "note" not in occ
    assert occ["occupied_count"] == 151
    assert occ["vacant_count"] == 13


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
    assert "User: but this is the same as with 15 vacant" in text
    assert text.rfind("User: but this is the same as with 15 vacant") > text.rfind(
        "User: does in-place include vacant?"
    )


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


def test_compute_income_full_occupancy_uses_vacant_asking(monkeypatch):
    reset_turn("tester")
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Bay",
        units=[
            UnitRow(unit_id="1", in_place_rent=1000, vacant=False, occupancy="Occupied"),
            UnitRow(unit_id="2", in_place_rent=1000, vacant=False, occupancy="Occupied"),
            UnitRow(
                unit_id="3",
                in_place_rent=0,
                vacant=True,
                occupancy="Vacant",
                charge_codes={"_market_rent": 1200},
            ),
        ],
    )
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    income = json.loads(compute_income())
    assert income["in_place_monthly"] == 2000
    assert income["mean_occupied_rent"] == 1000.0
    assert income["vacant_asking_monthly"] == 1200.0
    assert income["estimated_full_occupancy_monthly"] == 3200.0
    assert "vacant_asking" in income["estimated_full_occupancy_method"]


def test_compute_income_vacant_fill_rent_scenario(monkeypatch):
    reset_turn("tester")
    occupied = [
        UnitRow(unit_id=str(i), in_place_rent=800, vacant=False, occupancy="Occupied")
        for i in range(151)
    ]
    vacant = [
        UnitRow(unit_id=f"v{i}", in_place_rent=0, vacant=True, occupancy="Vacant")
        for i in range(13)
    ]
    roll = CanonicalRentRoll(layout="one_row", property_name="Hill", units=occupied + vacant)
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    income = json.loads(compute_income(vacant_fill_rent=5000))
    assert income["in_place_monthly"] == 151 * 800
    assert income["in_place_annual"] == 151 * 800 * 12
    assert income["vacant_fill_rent"] == 5000
    assert income["vacant_fill_monthly"] == 13 * 5000
    assert income["vacant_fill_annual"] == 13 * 5000 * 12
    assert income["scenario_full_occupancy_monthly"] == 151 * 800 + 13 * 5000
    assert income["scenario_full_occupancy_annual"] == (151 * 800 + 13 * 5000) * 12
    from cre_rentroll.eval_service import vault_calc_ok

    draft = (
        "If all units are occupied, including the 13 vacant units at $5,000 per month, "
        f"the total annual income would be ${income['scenario_full_occupancy_annual']:,.0f}, "
        f"including in-place ${income['in_place_annual']:,.0f} plus "
        f"${income['vacant_fill_annual']:,.0f} from vacant fill."
    )
    traces = [{"name": "compute_income", "result": income}]
    assert vault_calc_ok(draft, roll, traces) is not False
