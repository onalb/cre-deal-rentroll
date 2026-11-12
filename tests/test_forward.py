"""The deal forwarder stamps hidden fields from the tools/list schema."""

from __future__ import annotations

import json

from cre_rentroll import turn_ctx
from cre_rentroll.forward import deal_call, model_schema, stamp_arguments, tool_function


def _units() -> dict:
    return {
        "name": "query_units",
        "description": "Vault unit counts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "occupancy": {"type": "string"},
                "username": {"type": "string"},
                "as_of": {"type": "string"},
                "route": {"type": "string"},
            },
            "required": ["username"],
        },
    }


def _plan() -> dict:
    return {
        "name": "create_plan",
        "description": "Store this turn's checklist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {"type": "string"},
                "checklist": {"type": "array"},
                "last_work_tool": {"type": "string"},
                "route": {"type": "string"},
            },
        },
    }


def test_model_schema_hides_session_fields():
    shown = model_schema(_units())
    assert list(shown["properties"]) == ["occupancy", "as_of"]
    assert "required" not in shown


def test_stamp_fills_route_username_and_as_of():
    stamped = stamp_arguments(
        _units(),
        {"occupancy": "all"},
        route="deal",
        username="ada",
        as_of="2024-06-01",
    )
    assert stamped["route"] == "deal"
    assert stamped["username"] == "ada"
    assert stamped["as_of"] == "2024-06-01"
    assert stamped["occupancy"] == "all"
    assert "checklist" not in stamped


def test_stamp_attaches_the_page_session_roll():
    from cre_rentroll import turn_ctx, vault
    from cre_rentroll.schemas import CanonicalRentRoll, UnitRow

    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Chambers",
        units=[UnitRow(unit_id="1")],
    )
    spec = _units()
    spec["input_schema"]["properties"]["roll"] = {"type": "object"}
    turn_ctx.set_chat_session("visit-a")
    try:
        vault.save_roll("ada", roll)
        stamped = stamp_arguments(spec, {"occupancy": "all"}, route="deal", username="ada")
    finally:
        turn_ctx.set_chat_session(None)
    assert stamped["roll"]["property_name"] == "Chambers"
    assert "roll" not in model_schema(spec)["properties"]


def test_stamp_attaches_checklist_only_when_the_schema_has_it():
    stamped = stamp_arguments(
        _plan(),
        {"tasks": "1. query_units"},
        route="deal",
        checklist=[{"text": "query_units", "status": "pending"}],
        work={"last_work_tool": "query_units", "last_work_status": "ok", "ran_ok": ["query_units"]},
    )
    assert stamped["checklist"] == [{"text": "query_units", "status": "pending"}]
    assert stamped["last_work_tool"] == "query_units"
    assert "username" not in stamped


def test_visible_signature_omits_hidden_arguments():
    fn = tool_function(_units(), lambda name, args: "")
    params = list(fn.__signature__.parameters)
    assert params == ["occupancy", "as_of"]


def test_deal_call_stores_the_returned_checklist(monkeypatch):
    turn_ctx.reset_turn("ada")
    turn_ctx.set_eval_query("how many units")

    def fake(name, arguments, timeout=180.0):
        assert arguments["route"] == "deal"
        assert arguments["checklist"] == []
        return json.dumps(
            {
                "ok": True,
                "text": "Plan stored.",
                "checklist": [{"text": "query_units", "status": "pending"}],
                "plan": "1. query_units",
            }
        )

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)
    text = deal_call("create_plan", {"tasks": "1. query_units"}, [_plan()])
    assert text == "Plan stored."
    assert turn_ctx.checklist() == [{"text": "query_units", "status": "pending"}]


def test_agentcore_deal_call_reads_the_page_roll(monkeypatch):
    from cre_rentroll.run_events import bind, reset
    from cre_rentroll.schemas import CanonicalRentRoll, UnitRow
    from cre_rentroll.vault import save_roll

    url = "https://credealrentroll.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
    monkeypatch.setenv("MCP_URL", url)
    monkeypatch.setenv("AGENTCORE_GATEWAY_URL", url)

    def boom(*_a, **_k):
        raise AssertionError("query_units must not call the AgentCore gateway")

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", boom)
    turn_ctx.reset_turn("ada")
    turn_ctx.set_chat_session("visit-bay")
    save_roll(
        "ada",
        CanonicalRentRoll(
            property_name="Bay View",
            as_of="2021-09-30",
            layout="one_row",
            units=[
                UnitRow(unit_id="1", occupancy="Occupied", in_place_rent=1000, sqft=400, mtm=True),
                UnitRow(unit_id="2", occupancy="Occupied", in_place_rent=900, sqft=500),
            ],
        ),
    )
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        text = deal_call("query_units", {}, [_units()])
    finally:
        reset(token)
    body = json.loads(text)
    assert body["unit_count"] == 2
    assert body["mtm_count"] == 1
    vault_lines = [row["message"] for row in seen if row["step"] == "vault"]
    assert vault_lines
    assert "in-process" in vault_lines[0]
    assert "roll=yes" in vault_lines[0]
    assert "units=2" in vault_lines[0]
