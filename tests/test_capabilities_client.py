"""Gateway URL set vs unset. Capability tools only POST /mcp/deal."""

from __future__ import annotations

import json

import httpx

from cre_rentroll.agents import BOOK, load_skill_text
from cre_rentroll.capabilities_client import GATEWAY_REQUIRED, apply_gateway_menu, call_deal_tool, deal_mcp_url
from cre_rentroll.chips import filter_book_tools
from cre_rentroll.tools import ask_prediction_agent, ask_vacancy_agent, query_units
from cre_rentroll.turn_ctx import reset_turn


def test_menu_keeps_deal_tool_names_when_unset(monkeypatch):
    monkeypatch.delenv("CAPABILITIES_GATEWAY_URL", raising=False)
    names = filter_book_tools(BOOK.tool_names, "", "how many units")
    assert "ask_vacancy_agent" in names
    assert "ask_prediction_agent" not in names
    assert "ask_vacancy" not in names
    assert "create_plan" in names
    assert "query_units" in names
    assert deal_mcp_url() == ""
    assert apply_gateway_menu(BOOK.tool_names) == BOOK.tool_names


def test_menu_keeps_the_same_names_when_gateway_is_set(monkeypatch):
    monkeypatch.setenv("CAPABILITIES_GATEWAY_URL", "http://127.0.0.1:8010")
    names = filter_book_tools(BOOK.tool_names, "", "how many units")
    assert "ask_vacancy_agent" in names
    assert "ask_vacancy" not in names
    assert "create_plan" in names
    assert "score_gcr" in names
    assert "parse_rent_roll_file" not in names
    assert "fit_vacancy" not in names
    assert deal_mcp_url() == "http://127.0.0.1:8010/mcp/deal"
    assert apply_gateway_menu(BOOK.tool_names) == BOOK.tool_names


def test_query_units_requires_gateway_when_unset(monkeypatch):
    monkeypatch.delenv("CAPABILITIES_GATEWAY_URL", raising=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("gateway must not be called")

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", boom)
    reset_turn("nobody")
    body = json.loads(query_units())
    assert body["ok"] is False
    assert body["error"] == GATEWAY_REQUIRED
    assert "capabilities gateway is required" in body["error"]


def test_query_units_posts_deal_tool_when_set(monkeypatch):
    monkeypatch.setenv("CAPABILITIES_GATEWAY_URL", "http://127.0.0.1:8010")
    seen: dict = {}

    def fake(name, arguments, timeout=180.0):
        seen["name"] = name
        seen["arguments"] = arguments
        return json.dumps({"unit_count": 4, "occupied_count": 4, "ok": True})

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)
    reset_turn("ada")
    body = json.loads(query_units())
    assert body["unit_count"] == 4
    assert seen["name"] == "query_units"
    assert seen["arguments"]["username"] == "ada"


def test_floor_and_prediction_tools_post_their_names(monkeypatch):
    monkeypatch.setenv("CAPABILITIES_GATEWAY_URL", "http://127.0.0.1:8010")
    seen: list[str] = []

    def fake(name, arguments, timeout=180.0):
        seen.append(name)
        if name == "ask_vacancy_agent":
            return json.dumps({"ok": True, "answer": "A1 is the vacant plan.", "plans": []})
        return json.dumps({"ok": True, "answer": "not trained", "awaiting_followup": True})

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)
    reset_turn("ada")
    floor = json.loads(ask_vacancy_agent("A1"))
    pred = json.loads(ask_prediction_agent(12, "what occupancy do you predict?"))
    assert floor["answer"].startswith("A1")
    assert pred["answer"] == "not trained"
    assert seen == ["ask_vacancy_agent", "ask_prediction_agent"]


def test_skills_come_from_gateway_when_set(monkeypatch):
    monkeypatch.setenv("CAPABILITIES_GATEWAY_URL", "http://127.0.0.1:8010")
    requested: list[str] = []

    class Response:
        text = "# from-gateway\n"

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, **_kwargs):
        requested.append(url)
        return Response()

    monkeypatch.setattr(httpx, "get", fake_get)
    text = load_skill_text(("citation-policy", "underwriting"))
    assert requested == [
        "http://127.0.0.1:8010/skills/citation-policy",
        "http://127.0.0.1:8010/skills/underwriting",
    ]
    assert "from-gateway" in text


def test_skills_stay_on_disk_when_unset(monkeypatch):
    monkeypatch.delenv("CAPABILITIES_GATEWAY_URL", raising=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("disk skills must not HTTP")

    monkeypatch.setattr(httpx, "get", boom)
    text = load_skill_text(("citation-policy",))
    assert "citation-policy" in text
    assert "Cite" in text or "cite" in text.lower()


class _RpcResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_call_deal_tool_posts_mcp_deal(monkeypatch):
    monkeypatch.setenv("CAPABILITIES_GATEWAY_URL", "http://127.0.0.1:8010")
    urls: list[str] = []
    names: list[str] = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, **kwargs):
            urls.append(url)
            body = kwargs.get("json") or {}
            method = body.get("method")
            if method == "initialize":
                return _RpcResponse({"jsonrpc": "2.0", "id": 1, "result": {}})
            if method == "notifications/initialized":
                return _RpcResponse({})
            names.append(body["params"]["name"])
            return _RpcResponse(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"content": [{"type": "text", "text": "{\"ok\": true}"}]},
                }
            )

    monkeypatch.setattr(httpx, "Client", Client)
    text = call_deal_tool("query_units", {"username": "ada"})
    assert text == "{\"ok\": true}"
    assert urls
    assert all(url == "http://127.0.0.1:8010/mcp/deal" for url in urls)
    assert names == ["query_units"]
