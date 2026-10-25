"""MCP client for the vacancy prediction agent on localhost:8000.

Rentroll does not bind 8000 and never uploads or trains. Training is only
via the vacancy-agent UI (file picker + Send rent roll). External callers
use streamable-HTTP MCP at {RR_PREDICT_URL}/mcp tool ask_vacancy. The
vacancy UI may still use SSE POST /api/chat; Rentroll must not.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx

_state: dict[str, dict[str, Any]] = {}

_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-03-26",
}


def predict_base_url() -> str:
    return os.getenv("RR_PREDICT_URL", "http://127.0.0.1:8000").rstrip("/")


def mcp_url() -> str:
    return f"{predict_base_url()}/mcp"


def reset_predict_state() -> None:
    _state.clear()


def _session(username: str) -> dict[str, Any]:
    row = _state.get(username)
    if not row:
        row = {"session_id": str(uuid.uuid4())}
        _state[username] = row
    return row


def _rpc_payload(data: dict) -> dict:
    if data.get("error"):
        err = data["error"]
        if isinstance(err, dict):
            raise RuntimeError(err.get("message") or json.dumps(err))
        raise RuntimeError(str(err))
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def _parse_json_rpc(response: httpx.Response) -> dict:
    response.raise_for_status()
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError("prediction agent returned non-JSON MCP response") from exc
    if not isinstance(data, dict):
        raise RuntimeError("prediction agent returned a non-object MCP response")
    return _rpc_payload(data)


def _content_text(result: dict) -> str:
    content = result.get("content")
    if isinstance(content, list) and content:
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return "\n".join(parts)
    structured = result.get("structuredContent")
    if isinstance(structured, str) and structured:
        return structured
    if isinstance(structured, dict) and structured.get("result"):
        return str(structured["result"])
    if isinstance(structured, dict) and structured.get("text"):
        return str(structured["text"])
    text = result.get("text")
    return str(text) if text else ""


def _tool_text(result: dict) -> str:
    if result.get("isError"):
        raise RuntimeError(_content_text(result) or "prediction agent error")
    return _content_text(result)


def _post_rpc(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    method: str,
    params: dict | None = None,
    req_id: int | str | None = 1,
) -> tuple[dict, dict[str, str]]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        body["id"] = req_id
    if params is not None:
        body["params"] = params
    response = client.post(url, json=body, headers=headers)
    session = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id")
    if session:
        headers = {**headers, "Mcp-Session-Id": session}
    if req_id is None:
        if response.status_code >= 400:
            response.raise_for_status()
        return {}, headers
    return _parse_json_rpc(response), headers


def chat(username: str, message: str, timeout: float = 180.0) -> dict:
    state = _session(username)
    url = mcp_url()
    headers = dict(_MCP_HEADERS)
    try:
        with httpx.Client(timeout=timeout) as client:
            _, headers = _post_rpc(
                client,
                url,
                headers,
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "cre-deal-rentroll", "version": "0.1.0"},
                },
                req_id=1,
            )
            _post_rpc(
                client,
                url,
                headers,
                "notifications/initialized",
                req_id=None,
            )
            result, _ = _post_rpc(
                client,
                url,
                headers,
                "tools/call",
                {
                    "name": "ask_vacancy",
                    "arguments": {
                        "question": message,
                        "session_id": state["session_id"],
                    },
                },
                req_id=2,
            )
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 — same surface as the old HTTP client
        raise RuntimeError(str(exc)) from exc
    answer = _tool_text(result)
    return {"message": answer, "done": result, "session_id": state["session_id"]}


def forecast(username: str, question: str, horizon_months: int = 12) -> dict:
    prompt = (question or "").strip() or (
        f"Over the next {horizon_months} months, how many units expire or are MTM, "
        "what monthly income is at risk, and what occupancy do you predict?"
    )
    result = chat(username, prompt)
    return {
        "source": "vacancy-prediction-agent",
        "url": predict_base_url(),
        "horizon_months": horizon_months,
        "answer": result["message"],
        "ok": True,
    }
