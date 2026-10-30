"""Planless routing: single-tool asks skip create_plan; the guard blocks a
second distinct work tool until update_plan seeds a checklist."""

from __future__ import annotations

import json

import pytest

from cre_rentroll import turn_ctx
from cre_rentroll.planless import single_tool_intent, this_roll_needs_file
from cre_rentroll.tools import TOOL_IMPLS


def test_single_tool_routing_table():
    cases = [
        # (message, has_vault, expected)
        ("what is the occupancy right now", True, "query_units"),
        ("whats the occupancy", True, "query_units"),
        ("how many units are vacant", True, "query_units"),
        ("whats the mtm count", True, "query_units"),
        ("whats the typical unit size", True, "query_units"),
        (
            "What's the unit count, occupancy, in-place monthly income, vacant count, average unit sqft, and any month-to-month on this rent roll?",
            True,
            "query_units",
        ),
        ("whats the occupancy and income", True, "query_units"),
        ("whats our in-place monthly income", True, "compute_income"),
        ("What's the annualized in-place income?", True, "compute_income"),
        ("show me the map", True, "osm_geocode"),
        ("where is this property", True, "osm_geocode"),
        ("what is a rent roll", False, "web_search"),
        ("average property value in that area", True, "web_search"),
        ("comps in that metro", True, "web_search"),
        ("What occupancy do you predict for Bay View Gardens Apartments?", True, "ask_prediction_agent"),
        ("What is the predicted vacancy for Bay View Gardens Apartments?", True, "ask_prediction_agent"),
        ("predict occupancy for 12 months", True, "ask_prediction_agent"),
        ("mtm waterfall for next year", True, "ask_prediction_agent"),
    ]
    for message, has_vault, expected in cases:
        assert single_tool_intent(message, has_vault=has_vault) == expected
    assert (
        single_tool_intent("1x1 (renovated)", has_vault=True, predict_followup=True)
        == "ask_prediction_agent"
    )
    assert (
        single_tool_intent("0", has_vault=True, predict_followup=True)
        == "ask_prediction_agent"
    )
    assert single_tool_intent("1x1 (renovated)", has_vault=True) is None


def test_empty_vault_this_roll_needs_file():
    assert this_roll_needs_file("whats the occupancy", has_vault=False) is True
    assert this_roll_needs_file(
        "What's the unit count, occupancy, in-place monthly income, vacant count",
        has_vault=False,
    ) is True
    assert this_roll_needs_file("whats the occupancy", has_vault=True) is False
    assert this_roll_needs_file("what is a rent roll", has_vault=False) is False
    assert this_roll_needs_file("show me the map", has_vault=False) is False
    assert this_roll_needs_file("average property value in that area", has_vault=False) is False
    assert this_roll_needs_file("What occupancy do you predict?", has_vault=False) is False


def test_ambiguous_asks_stay_planned():
    cases = [
        ("show me the map and the cap rate", True),
        ("Where is this property? Can you put it on a map, and how many units are vacant, and what is occupancy?", True),
        ("What is in-place monthly income, and what would annualized NOI be if opex is 35% of EGI?", True),
        ("no actually its in the wrong country", True),
        ("whats the occupancy", False),  # need-file path, not web_search
        ("define cap rate", True),
        ("how are you", True),
    ]
    for message, has_vault in cases:
        assert single_tool_intent(message, has_vault=has_vault) is None


def test_guard_blocks_second_distinct_work_tool():
    turn_ctx.reset_turn("ada")
    turn_ctx.set_planless("query_units")
    blocked = json.loads(TOOL_IMPLS["web_search"](query="cap rates"))
    assert blocked["ok"] is False
    assert blocked["rejected"] is True
    assert "update_plan" in blocked["error"]


def test_guard_allows_routed_tool_and_unblocks_after_seed():
    turn_ctx.reset_turn("ada")
    turn_ctx.set_planless("web_search")
    # Routed tool passes the guard; it then raises the gateway-unset error
    # (web_search's contract), NOT the planless rejection.
    with pytest.raises(RuntimeError) as exc:
        TOOL_IMPLS["web_search"](query="cap rates")
    assert "Planless" not in str(exc.value)
    # After update_plan seeds a checklist, any work tool passes the guard
    # (query_units returns the gateway-unset error JSON, not a rejection).
    turn_ctx.set_checklist([{"text": "query_units", "status": "in_progress"}])
    err = json.loads(TOOL_IMPLS["query_units"]())
    assert err.get("rejected") is not True


def test_guard_inactive_when_not_planless():
    turn_ctx.reset_turn("ada")
    with pytest.raises(RuntimeError) as exc:
        TOOL_IMPLS["web_search"](query="cap rates")
    assert "Planless" not in str(exc.value)


def test_harness_menu_drops_plan_tools_on_planless_turns():
    from cre_rentroll.harness import session_prompt
    from cre_rentroll.agents import system_prompt, Inventory

    prompt = session_prompt("whats the occupancy", planless="query_units")
    assert "PLANLESS" in prompt
    assert "first tool is always create_plan" not in prompt
    planned = session_prompt("occupancy and income and cap", planless=None)
    assert "first tool is always create_plan" in planned
    oos = session_prompt("0", cre_ask=False)
    assert "first tool is always create_plan" not in oos

    planless_inv = Inventory("DealRentroll", ("update_plan", "reflection", "score_gcr", "query_units"), ())
    text = system_prompt(planless_inv, "")
    assert "planless" in text.lower()
    assert "first create_plan checklist" not in text

    oos_inv = Inventory("DealRentroll", (), ())
    oos_text = system_prompt(oos_inv, "")
    assert "planless" not in oos_text.lower()
    assert "always call create_plan" not in oos_text.lower()
    assert "own words" in oos_text.lower()