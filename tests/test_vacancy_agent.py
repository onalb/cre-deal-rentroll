"""Deal stubs call /mcp/deal. They do not score a floor plan locally."""

import inspect
import json

from cre_rentroll.capabilities_client import GATEWAY_REQUIRED
from cre_rentroll.tools import TOOL_IMPLS, ask_vacancy_agent
from cre_rentroll.turn_ctx import reset_turn


def test_ask_vacancy_agent_requires_gateway_when_unset(monkeypatch):
    monkeypatch.delenv("CAPABILITIES_GATEWAY_URL", raising=False)
    reset_turn("ada")
    body = json.loads(ask_vacancy_agent("1x1 Reno"))
    assert body["ok"] is False
    assert body["error"] == GATEWAY_REQUIRED
    assert "capabilities gateway is required" in body["error"]


def test_ask_vacancy_agent_posts_floor_plan(monkeypatch):
    monkeypatch.setenv("CAPABILITIES_GATEWAY_URL", "http://127.0.0.1:8010")
    seen: dict = {}

    def fake(name, arguments, timeout=180.0):
        seen["name"] = name
        seen["arguments"] = arguments
        return json.dumps(
            {
                "agent": "vacancy",
                "ok": True,
                "plans": [{"plan": "1x1 Reno", "probability": 0.4}],
                "answer": "1x1 Reno is vacant with probability 0.4.",
            }
        )

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)
    reset_turn("ada")
    payload = json.loads(ask_vacancy_agent("1x1 Reno"))
    assert payload["plans"][0]["plan"] == "1x1 Reno"
    assert seen["name"] == "ask_vacancy_agent"
    assert seen["arguments"]["floor_plan"] == "1x1 Reno"
    assert seen["arguments"]["username"] == "ada"
    assert TOOL_IMPLS["ask_vacancy_agent"] is ask_vacancy_agent
    source = inspect.getsource(ask_vacancy_agent)
    assert "VacancyAgent" not in source
    assert "_sigmoid" not in source
    assert "load_roll" not in source
