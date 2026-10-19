import json

from cre_rentroll.harness import _user_facing_draft
from cre_rentroll.ingest import as_of_from_message
from cre_rentroll.present import answer_prose, is_operator_leak, last_scored_prose, user_visible
from cre_rentroll.turn_ctx import reset_turn


def test_hides_eval_retry_and_eval_banner():
    raw = (
        "Abstract this.\n\n[eval retry: fix vault/calc grounding and cover the request]"
    )
    assert "[eval retry" not in user_visible(raw)
    geo = """Mapped to 1 Main St (1.0, 2.0).

— Warning: live eval G=5 request-C=3 R=5 (score 3 band)."""
    prose = answer_prose(geo)
    assert "Warning" not in prose
    assert "Mapped to 1 Main St" in prose


def test_answer_prose_does_not_pick_fields_from_the_question():
    text = '{"property_name":"Hill","unit_count":252,"vacant_count":29}'
    q = "please give me only the vacant number of units"
    assert answer_prose(text, question=q) == text


def test_operator_leak_detects_need_replan_hint():
    leak = (
        "Need replan: call update_plan (MCP) with the NEW remaining work. "
        "Completed/incomplete rows stay on the list."
    )
    assert is_operator_leak(leak)
    assert not is_operator_leak("Search is unavailable; I cannot fetch current articles.")
    assert not is_operator_leak("Recent multifamily cap rates in Miami averaged 5.8%.")
    assert not is_operator_leak("The property is at 1 Main Street (25.7, -80.2).")


def test_operator_leak_detects_plan_json_inventory():
    plan = (
        "1. osm_geocode\n"
        "2. query_units\n"
        "Current step: [in_progress]\n"
        "Next steps: [pending]\n"
        "I will now locate the address and retrieve the unit counts."
    )
    assert is_operator_leak(plan)
    assert is_operator_leak("1. query_units\n2. compute_income")
    assert is_operator_leak(
        '{"property_name":"Hill","unit_count":252,"occupancy_pct":0.88,"units":[]}'
    )
    assert is_operator_leak("inventory no tools (non-CRE ask)")
    assert is_operator_leak("[tag: in_progress] [pending]")
    assert is_operator_leak("I will conduct a search to define a rent roll.")
    assert is_operator_leak(
        "I will conduct a search to define a rent roll.\n[tag: in_progress] [pending]"
    )


def test_eval_json_is_operator_leak_and_not_user_facing():
    reset_turn("tester")
    definition = (
        "A rent roll is a schedule of tenants, rents, and lease terms for a "
        "property, used to underwrite in-place income. [1]"
    )
    eval_obj = {
        "R": 5,
        "G": 5,
        "request_C": 5,
        "vault_calc_ok": True,
        "vault_invented": [],
        "vault_calc": "ok",
        "judge": "llm",
        "decision": "ok",
        "hint": "ok to show this sentence",
        "ok": True,
        "reason": "cited definition",
    }
    leak = json.dumps(eval_obj)
    assert is_operator_leak(leak)
    traces = [{"name": "score_gcr", "args": {"draft": definition}, "result": eval_obj}]
    assert last_scored_prose(traces) == definition
    mixed = f"{definition}\n\n{leak}"
    shown_mixed = _user_facing_draft(mixed, traces)
    assert "schedule of tenants" in shown_mixed
    assert "request_C" not in shown_mixed
    assert "ok to show this sentence" not in shown_mixed
    shown_json = _user_facing_draft(leak, traces)
    assert shown_json == definition
    assert "request_C" not in shown_json
    shown_empty = _user_facing_draft(leak, [])
    assert "could not finish" in shown_empty.lower()
    assert "request_C" not in shown_empty
    assert "schedule of tenants" not in shown_empty


def test_as_of_from_question_not_today():
    q = (
        "Abstract this rent roll: unit count, occupancy, in-place monthly income, "
        "vacant count, MTM if present as of 01/01/2021. Cite computed totals."
    )
    assert as_of_from_message(q) == "2021-01-01"
    q2 = (
        "Abstract this rent roll: unit count, occupancy, in-place monthly income, "
        "vacant count, MTM if present. Cite computed totals. Do not geocode or search."
    )
    assert as_of_from_message(q2) is None
