"""Generic tool-failure behavior: no loop, no operator leak, no canned copy."""

from cre_rentroll.agents import BOOK, system_prompt
from cre_rentroll.harness import _user_facing_draft, run_task
from cre_rentroll.present import is_operator_leak
from cre_rentroll.reflect import on_tool_error
from cre_rentroll.run_events import bind, reset
from cre_rentroll.turn_ctx import record, reset_turn

_CAP_COPY = (
    "I couldn't look up recent multifamily cap rates or sales news because "
    "web search is unavailable. I can't compare this property to current "
    "metro comps from the web right now."
)
_LEAK = (
    "Need replan: call update_plan (MCP) with the NEW remaining work. "
    "Completed/incomplete rows stay on the list. Do not call create_plan (MCP) again."
)
_SEARCH_ERR = {
    "name": "web_search",
    "args": {"query": "miami"},
    "result": {"ok": False, "error": "web_search failed (network)"},
}


def _assert_no_cap_copy(text: str) -> None:
    low = (text or "").lower()
    assert _CAP_COPY.lower() not in low
    assert "multifamily cap rates" not in low
    assert "metro comps" not in low


def test_failed_tool_note_does_not_loop_or_inject_copy():
    note = on_tool_error("web_search", "network")
    assert "failed" in note.lower()
    assert "without looping" in note.lower()
    _assert_no_cap_copy(note)


def test_user_facing_draft_replaces_need_replan_leak_after_failed_tool():
    reset_turn("tester")
    assert is_operator_leak(_LEAK)
    out = _user_facing_draft(_LEAK, [_SEARCH_ERR])
    _assert_no_cap_copy(out)
    assert "update_plan" not in out
    assert "Need replan" not in out
    assert "could not finish" in out.lower()


def test_user_facing_draft_strips_search_narration_and_status_tags():
    reset_turn("tester")
    leak = "I will conduct a search to define a rent roll.\n[tag: in_progress] [pending]"
    assert is_operator_leak(leak)
    assert is_operator_leak("[tag: in_progress] [pending]")
    assert is_operator_leak("I will conduct a search to define a rent roll.")
    out = _user_facing_draft(leak, [])
    assert "I will conduct a search" not in out
    assert "[tag: in_progress]" not in out
    assert "[pending]" not in out
    assert "could not finish" in out.lower()


def test_user_facing_draft_strips_plan_checklist_leak():
    reset_turn("tester")
    leak = (
        "1. osm_geocode\n"
        "2. query_units\n"
        "Current step: [in_progress]\n"
        "Next steps: [pending]\n"
        "I will now locate the address and retrieve the unit counts."
    )
    assert is_operator_leak(leak)
    out = _user_facing_draft(leak, [])
    assert "1. osm_geocode" not in out
    assert "query_units" not in out
    assert "Current step" not in out
    assert "could not finish" in out.lower()


def test_prompt_forbids_search_retry_and_operator_leak():
    sys = system_prompt(BOOK, "").lower()
    assert "do not call web_search again" in sys
    assert "never copy need replan" in sys
    assert "python does not write that answer" in sys
    assert "you write a short user-facing sentence" in sys
    assert "only if this user question asked" in sys
    assert _CAP_COPY.lower() not in sys
    assert "occupancy, rent-roll definitions" in sys


def _stub_in_scope_run(monkeypatch, question: str, draft: str, *, vaulted: bool):
    from cre_rentroll.schemas import CanonicalRentRoll, EvalScores, UnitRow

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(G=5, request_C=5, R=5, judge="heuristic"),
    )
    if vaulted:
        roll = CanonicalRentRoll(
            layout="one_row",
            property_name="Hill",
            units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
        )
        monkeypatch.setattr("cre_rentroll.harness.vault.load_session", lambda u: {"messages": []})
        monkeypatch.setattr("cre_rentroll.harness.vault.load_roll", lambda u: roll)
    else:
        monkeypatch.setattr("cre_rentroll.harness.vault.load_session", lambda u: {"messages": []})
        monkeypatch.setattr("cre_rentroll.harness.vault.load_roll", lambda u: None)

    def fake_agent(*_a, **_k):
        record(
            "web_search",
            {"query": question},
            {"ok": False, "error": "web_search failed (network)"},
        )
        return draft

    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    seen = []
    token = bind(seen.append)
    try:
        out = run_task("analyst", question)
    finally:
        reset(token)
    return out, seen


def test_occupancy_with_failed_search_does_not_inject_cap_copy(monkeypatch):
    out, seen = _stub_in_scope_run(
        monkeypatch, "What is occupancy?", _LEAK, vaulted=True
    )
    _assert_no_cap_copy(out.text)
    assert "Need replan" not in out.text
    assert any("web_search" in (row.get("message") or "") for row in seen)


def test_rent_roll_definition_with_failed_search_does_not_inject_cap_copy(monkeypatch):
    out, _seen = _stub_in_scope_run(
        monkeypatch, "What's a rent roll?", _LEAK, vaulted=False
    )
    _assert_no_cap_copy(out.text)


def test_occupancy_good_draft_kept_when_search_fails(monkeypatch):
    out, _seen = _stub_in_scope_run(
        monkeypatch,
        "What is occupancy?",
        "Occupancy is 100% on this rent roll.",
        vaulted=True,
    )
    assert out.text == "Occupancy is 100% on this rent roll."
    _assert_no_cap_copy(out.text)