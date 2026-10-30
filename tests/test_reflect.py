from cre_rentroll.harness import _draft_log, run_task
from cre_rentroll.reflect import on_tool_error
from cre_rentroll.run_events import annotate_message, bind, reset
from cre_rentroll.schemas import CanonicalRentRoll, UnitRow
from cre_rentroll.tools import create_plan, reflection


def _roll() -> CanonicalRentRoll:
    return CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )


def _canned_leftover(msgs: list[str]) -> list[str]:
    needles = (
        "finished with an empty draft",
        "Carry citations forward",
        "Reflect if one of four",
        "skip if ReAct already did",
        "on_subgoal_done",
    )
    return [m for m in msgs if any(n in m for n in needles)]


def test_on_tool_error_names_the_failed_tool():
    note = on_tool_error("web_search", "forced failure")
    assert "web_search failed" in note
    assert "Continue without looping" in note


def test_no_canned_leftover_reflect_when_reflection_tool_used(monkeypatch):
    roll = _roll()
    seen: list[dict] = []
    token = bind(seen.append)

    def fake_agent(*_a, **_k):
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

    reflect_msgs = [row["message"] for row in seen if row["step"] == "reflect"]
    all_msgs = [row["message"] for row in seen]
    assert _canned_leftover(all_msgs) == []
    assert not any("RollAbstractor" in m and "empty draft" in m for m in reflect_msgs)
    assert any("empty draft; still need occupancy" in m for m in reflect_msgs)
    plan_msgs = [row["message"] for row in seen if row["step"] == "plan"]
    assert not any("second ReAct pass" in m for m in plan_msgs)
    assert not any("harness injection" in m.lower() for m in plan_msgs)


def test_no_canned_leftover_reflect_when_reflection_tool_skipped(monkeypatch):
    roll = _roll()
    seen: list[dict] = []
    token = bind(seen.append)
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", lambda *a, **k: "")
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

    reflect_msgs = [row["message"] for row in seen if row["step"] == "reflect"]
    all_msgs = [row["message"] for row in seen]
    assert _canned_leftover(all_msgs) == []
    assert not any("Carry citations" in m for m in reflect_msgs)
    assert not any("on_subgoal_done" in m for m in all_msgs)
    assert not any("finished with an empty draft" in m for m in reflect_msgs)
    assert not any("incomplete" in m.lower() and "occupancy" in m.lower() for m in reflect_msgs)


def test_draft_log_empty_and_prefixes():
    empty = _draft_log("RollAbstractor agent draft", "")
    assert empty == "RollAbstractor agent draft is empty (0 characters)."
    labeled = annotate_message(empty)
    assert labeled == "RollAbstractor (skill) agent draft is empty (0 characters)."
    before = _draft_log("Draft before eval", "")
    assert before == "Draft before eval is empty (0 characters)."
    raw = '{"property_name":"Hill","unit_count":1,"occupancy_pct":1.0,"in_place_monthly":1500}'
    fill = _draft_log("RollAbstractor agent draft", raw)
    assert fill.startswith("RollAbstractor agent draft:")
    assert raw in fill
    long_draft = "Occupancy is 92.07%. " + ("In-place monthly income is 1500. " * 40)
    logged = _draft_log("agent draft", long_draft)
    assert long_draft.strip() in logged
    assert "…" not in logged
    assert len(logged) > 360
