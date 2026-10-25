import json

import httpx
import pytest

from cre_rentroll.agents import BOOK
from cre_rentroll.mcp_runtime import VAULT_MCP, mcp_server_for
from cre_rentroll.predict_client import forecast, reset_predict_state
from cre_rentroll.tools import TOOL_IMPLS, ask_prediction_agent


def test_prediction_tool_is_on_the_book_menu():
    assert "ask_prediction_agent" in BOOK.tool_names
    assert "ask_vacancy" not in BOOK.tool_names
    assert TOOL_IMPLS["ask_prediction_agent"] is ask_prediction_agent
    assert mcp_server_for("ask_prediction_agent") == VAULT_MCP


def test_ask_prediction_agent_calls_http(monkeypatch):
    monkeypatch.setattr("cre_rentroll.tools.turn_ctx.username", lambda: "tester")
    recorded = {}
    monkeypatch.setattr(
        "cre_rentroll.tools.turn_ctx.record",
        lambda name, args, result: recorded.update(name=name, args=args, result=result),
    )
    monkeypatch.setattr(
        "cre_rentroll.predict_client.forecast",
        lambda user, question, horizon_months=12: {
            "ok": True,
            "source": "vacancy-prediction-agent",
            "answer": "12 expirations",
            "horizon_months": horizon_months,
        },
    )
    payload = json.loads(ask_prediction_agent(12, "forecast occupancy"))
    assert recorded["name"] == "ask_prediction_agent"
    assert payload["answer"] == "12 expirations"
    assert payload["source"] == "vacancy-prediction-agent"
    assert "train" not in payload


class FakeResponse:
    def __init__(self, payload: dict | None = None, status_code: int = 200, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://127.0.0.1:8000/mcp")
            raise httpx.HTTPStatusError(
                "error",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self) -> dict:
        return self._payload


def _ok_call_result(text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"content": [{"type": "text", "text": text}]},
    }


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        raise AssertionError("forecast must not stream SSE /api/chat")

    def post(self, url, **kwargs):
        body = kwargs.get("json") or {}
        self.calls.append({"method": "POST", "url": url, "json": body})
        if "/api/upload" in url or "/api/chat" in url:
            raise AssertionError("forecast must not POST /api/chat or /api/upload")
        method = body.get("method")
        if method == "initialize":
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}})
        if method == "notifications/initialized":
            return FakeResponse({})
        if method == "tools/call":
            assert body["params"]["name"] == "ask_vacancy"
            return FakeResponse(_ok_call_result("building not trained"))
        raise AssertionError(f"unexpected MCP method {method}")


def test_forecast_posts_mcp_ask_vacancy_not_chat(monkeypatch):
    reset_predict_state()
    client = FakeClient()

    def factory(*args, **kwargs):
        return client

    monkeypatch.setattr(httpx, "Client", factory)
    result = forecast("tester", "what occupancy do you predict?", horizon_months=12)
    assert result["ok"] is True
    assert result["answer"] == "building not trained"
    assert "train" not in result
    assert client.calls
    assert all(c["url"].endswith("/mcp") for c in client.calls)
    assert all("/api/chat" not in c["url"] for c in client.calls)
    assert all("/api/upload" not in c["url"] for c in client.calls)
    call = next(c for c in client.calls if c["json"].get("method") == "tools/call")
    payload = call["json"]["params"]
    assert payload["name"] == "ask_vacancy"
    args = payload["arguments"]
    assert args["question"] == "what occupancy do you predict?"
    assert args["session_id"]
    assert "file_id" not in args["question"]
    assert "Train this rent roll" not in args["question"]


def test_forecast_reuses_session_id(monkeypatch):
    reset_predict_state()
    client = FakeClient()
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)
    forecast("tester", "first")
    forecast("tester", "second")
    calls = [c for c in client.calls if c["json"].get("method") == "tools/call"]
    ids = [c["json"]["params"]["arguments"]["session_id"] for c in calls]
    assert len(ids) == 2
    assert ids[0] == ids[1]


def test_ask_prediction_agent_maps_mcp_failure(monkeypatch):
    monkeypatch.setattr("cre_rentroll.tools.turn_ctx.username", lambda: "tester")
    monkeypatch.setattr("cre_rentroll.tools.turn_ctx.record", lambda *a, **k: None)

    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("cre_rentroll.predict_client.forecast", boom)
    payload = json.loads(ask_prediction_agent(12, "forecast occupancy"))
    assert payload["ok"] is False
    assert "connection refused" in payload["error"]
    assert payload["source"] == "vacancy-prediction-agent"


def test_forecast_raises_on_http_error(monkeypatch):
    reset_predict_state()

    class FailClient(FakeClient):
        def post(self, url, **kwargs):
            self.calls.append({"method": "POST", "url": url, "json": kwargs.get("json")})
            return FakeResponse(status_code=500)

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: FailClient())
    with pytest.raises(RuntimeError):
        forecast("tester", "what occupancy do you predict?")
