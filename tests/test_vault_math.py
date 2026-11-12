"""AgentCore answers vault tools from the page roll. It does not call the gateway."""

from __future__ import annotations

import json

from cre_rentroll.schemas import CanonicalRentRoll, UnitRow
from cre_rentroll.tools import compute_income, query_units
from cre_rentroll.turn_ctx import reset_turn, set_chat_session
from cre_rentroll.vault import save_roll


def test_agentcore_query_units_uses_the_page_roll(monkeypatch):
    url = "https://credealrentroll.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
    monkeypatch.setenv("MCP_URL", url)
    monkeypatch.setenv("AGENTCORE_GATEWAY_URL", url)

    def boom(*_a, **_k):
        raise AssertionError("query_units must not call the AgentCore gateway")

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", boom)
    set_chat_session("visit-bay")
    reset_turn("ada")
    save_roll(
        "ada",
        CanonicalRentRoll(
            property_name="Bay View",
            as_of="2021-09-30",
            layout="one_row",
            units=[
                UnitRow(unit_id="1", occupancy="Occupied", in_place_rent=1000, sqft=400, mtm=True),
                UnitRow(unit_id="2", occupancy="Occupied", in_place_rent=2000, sqft=600),
                UnitRow(unit_id="3", occupancy="Vacant", in_place_rent=0, sqft=500),
            ],
        ),
    )
    out = json.loads(query_units())
    assert out["unit_count"] == 3
    assert out["vacant_count"] == 1
    assert out["occupancy_pct"] == 0.6667
    assert out["mtm_count"] == 1
    assert out["avg_sqft"] == 500.0
    income = json.loads(compute_income())
    assert income["in_place_monthly"] == 3000.0
    assert income["vacant_count"] == 1
