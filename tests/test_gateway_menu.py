"""Gateway-driven menu: the role-filtered tools/list answer is the base menu
when MCP is configured; BOOK_TOOLS is the offline fallback when it is not."""

from __future__ import annotations

from cre_rentroll.agents import BOOK
from cre_rentroll.harness import run_task
from cre_rentroll.predict_followup import reset_predict_state
from cre_rentroll.run_events import bind, reset
from cre_rentroll.schemas import CanonicalRentRoll, EvalScores, UnitRow


def _roll() -> CanonicalRentRoll:
    return CanonicalRentRoll(
        layout="one_row",
        property_name="Fixture Towers",
        as_of="2024-06-01",
        units=[
            UnitRow(unit_id="1", unit_type="Plan A", in_place_rent=1000, vacant=False),
            UnitRow(unit_id="2", unit_type="Plan B", in_place_rent=0, vacant=True),
        ],
    )


def _stub_turn(monkeypatch, draft: str, roll: CanonicalRentRoll | None = None) -> dict:
    reset_predict_state()
    monkeypatch.setattr("cre_rentroll.harness.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.harness.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.harness.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(R=4, G=4, request_C=4, vault_calc_ok=True, judge="llm"),
    )
    captured: dict = {}

    def fake_agent(_prompt, inv, _facts):
        captured["inv"] = inv
        return draft

    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    return captured


# Gateway names without a deal-app implementation must not reach the menu,
# and the predict gate still applies to the gateway answer (this ask is not
# forward-looking, so ask_prediction_agent drops).
_GATEWAY_LIST = [
    "cre_ping",
    "parse_rent_roll_file",
    "create_plan",
    "update_plan",
    "update_plan_status",
    "reflection",
    "query_units",
    "compute_income",
    "web_search",
    "ask_prediction_agent",
]

_GATEWAY_MENU = (
    "create_plan",
    "update_plan",
    "update_plan_status",
    "reflection",
    "query_units",
    "compute_income",
    "web_search",
)


def test_gateway_list_is_the_base_menu_when_mcp_configured(monkeypatch):
    monkeypatch.setenv("MCP_URL", "https://example.test/mcp")
    monkeypatch.setattr(
        "cre_rentroll.mcp_gateway.list_tools",
        lambda timeout=30.0: list(_GATEWAY_LIST),
    )
    captured = _stub_turn(monkeypatch, "Occupancy is 50 percent.", roll=_roll())
    seen = []
    token = bind(seen.append)
    try:
        run_task(
            "tester",
            "Where is this property? Can you put it on a map, and how many units are vacant?",
        )
    finally:
        reset(token)
    assert captured["inv"].tool_names == _GATEWAY_MENU


def test_gateway_menu_keeps_planless_filtering(monkeypatch):
    monkeypatch.setenv("MCP_URL", "https://example.test/mcp")
    monkeypatch.setattr(
        "cre_rentroll.mcp_gateway.list_tools",
        lambda timeout=30.0: list(_GATEWAY_LIST),
    )
    captured = _stub_turn(monkeypatch, "Occupancy is 50 percent.", roll=_roll())
    seen = []
    token = bind(seen.append)
    try:
        run_task("tester", "whats the occupancy")
    finally:
        reset(token)
    # Planless single-tool ask: create_plan / update_plan_status stay off the
    # gateway menu; update_plan remains the escape hatch.
    assert captured["inv"].tool_names == (
        "update_plan",
        "reflection",
        "query_units",
        "compute_income",
        "web_search",
    )


def test_offline_book_menu_when_mcp_unset(monkeypatch):
    monkeypatch.delenv("MCP_URL", raising=False)
    monkeypatch.delenv("AGENTCORE_GATEWAY_URL", raising=False)

    def boom(timeout=30.0):
        raise AssertionError("tools/list must not run when MCP_URL is unset")

    monkeypatch.setattr("cre_rentroll.mcp_gateway.list_tools", boom)
    captured = _stub_turn(monkeypatch, "Occupancy is 50 percent.", roll=_roll())
    seen = []
    token = bind(seen.append)
    try:
        run_task(
            "tester",
            "Where is this property? Can you put it on a map, and how many units are vacant?",
        )
    finally:
        reset(token)
    # Offline fallback: BOOK.tool_names (the ask is not forward-looking, so
    # the predict gate removes ask_prediction_agent).
    expected = tuple(n for n in BOOK.tool_names if n != "ask_prediction_agent")
    assert captured["inv"].tool_names == expected


def test_mcp_role_header_defaults_to_deal(monkeypatch):
    from cre_rentroll.mcp_gateway import mcp_role

    monkeypatch.delenv("MCP_ROLE", raising=False)
    assert mcp_role() == "deal"
    monkeypatch.setenv("MCP_ROLE", "vacancy")
    assert mcp_role() == "vacancy"