from cre_rentroll.agents import BOOK, system_prompt
from cre_rentroll.harness import _user_facing_draft
from cre_rentroll.present import is_operator_leak
from cre_rentroll.reflect import on_tool_error
from cre_rentroll.scope import SEARCH_DOWN_TEXT
from cre_rentroll.tools import create_plan, reflection, update_plan, web_search_failed
from cre_rentroll.turn_ctx import checklist, record, reset_turn


def test_search_down_replans():
    note = on_tool_error("web_search", "forced failure")
    assert "Replan" in note
    assert "loop" not in note.lower() or "not loop" in note.lower() or "do not loop" in note.lower()
    assert "unavailable" in note.lower()


def test_user_facing_draft_replaces_need_replan_leak_after_search_fail():
    reset_turn("tester")
    leak = (
        "Need replan: call update_plan (MCP) with the NEW remaining work. "
        "Completed/incomplete rows stay on the list. Do not call create_plan (MCP) again."
    )
    assert is_operator_leak(leak)
    traces = [
        {
            "name": "web_search",
            "args": {"query": "miami"},
            "result": {"ok": False, "error": "web_search forced failure (search-down fixture)"},
        }
    ]
    out = _user_facing_draft(leak, traces)
    assert out == SEARCH_DOWN_TEXT
    assert "update_plan" not in out
    assert "Need replan" not in out


def test_update_plan_after_failed_search_marks_incomplete_not_retry():
    reset_turn("tester")
    create_plan("1. [pending] Search for recent multifamily cap rates or sales in the Miami metro.")
    record(
        "web_search",
        {"query": "recent multifamily cap rates sales Miami"},
        {"ok": False, "error": "web_search forced failure (search-down fixture)"},
    )
    assert web_search_failed()
    out = update_plan("1. Search for recent multifamily cap rates or sales in the Miami metro.")
    assert "do not call web_search again" in out.lower()
    assert "unavailable" in out.lower()
    assert checklist()[0]["status"] == "incomplete"


def test_reflection_after_search_fail_does_not_ask_same_search():
    reset_turn("tester")
    record(
        "web_search",
        {"query": "x"},
        {"ok": False, "error": "web_search forced failure (search-down fixture)"},
    )
    msg = reflection("search failed", need_replan=True)
    assert "do not call web_search again" in msg.lower()
    assert "unavailable" in msg.lower()
    assert "do not copy this tool result" in msg.lower()


def test_prompt_forbids_search_retry_and_operator_leak():
    sys = system_prompt(BOOK, "").lower()
    assert "do not call web_search again" in sys
    assert "never copy need replan" in sys
    assert "python does not write that answer" in sys
    assert "you write a short user-facing sentence" in sys
