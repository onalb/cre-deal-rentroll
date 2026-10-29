"""Deal app client for the one AgentCore MCP.

Capability tools POST {MCP_URL} as the deal identity. Skills stay on
{CAPABILITIES_GATEWAY_URL}/skills. There is no in-process vault fallback.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

CAPABILITY_TOOLS = frozenset(
    {
        "query_units",
        "compute_income",
        "compute_noi",
        "compute_cap_rate",
        "osm_geocode",
        "web_search",
        "ask_vacancy_agent",
        "ask_prediction_agent",
    }
)

GATEWAY_REQUIRED = (
    "capabilities gateway is required (set CAPABILITIES_GATEWAY_URL)"
)

_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-03-26",
}


def gateway_base() -> str:
    return os.environ.get("CAPABILITIES_GATEWAY_URL", "").strip().rstrip("/")


def deal_mcp_url() -> str:
    from cre_rentroll.mcp_gateway import mcp_url

    return mcp_url()


def apply_gateway_menu(names: tuple[str, ...]) -> tuple[str, ...]:
    """Capability names stay the names the model already uses."""
    return tuple(names)


def _rpc_payload(data: dict) -> dict:
    if data.get("error"):
        err = data["error"]
        if isinstance(err, dict):
            raise RuntimeError(err.get("message") or json.dumps(err))
        raise RuntimeError(str(err))
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def _parse_json_rpc(response: httpx.Response) -> dict:
    if response.status_code in {301, 302, 303, 307, 308}:
        raise RuntimeError(
            f"MCP URL redirected ({response.status_code}); refusing to follow"
        )
    response.raise_for_status()
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError("capabilities gateway returned non-JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("capabilities gateway returned a non-object response")
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
    if isinstance(structured, dict) and structured.get("result") is not None:
        inner = structured["result"]
        return inner if isinstance(inner, str) else json.dumps(inner)
    if isinstance(structured, dict) and structured.get("text"):
        return str(structured["text"])
    text = result.get("text")
    return str(text) if text else ""


def _tool_text(result: dict) -> str:
    if result.get("isError"):
        raise RuntimeError(_content_text(result) or "capabilities gateway tool error")
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
    response = client.post(url, json=body, headers=headers, follow_redirects=False)
    session = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id")
    if session:
        headers = {**headers, "Mcp-Session-Id": session}
    if req_id is None:
        if response.status_code in {301, 302, 303, 307, 308}:
            raise RuntimeError(
                f"MCP URL redirected ({response.status_code}); refusing to follow"
            )
        if response.status_code >= 400:
            response.raise_for_status()
        return {}, headers
    return _parse_json_rpc(response), headers


def call_deal_tool(name: str, arguments: dict[str, Any], timeout: float = 180.0) -> str:
    """POST the one MCP tools/call as the deal identity. Does not follow redirects."""
    from cre_rentroll.mcp_gateway import call_tool, mcp_url

    if not mcp_url():
        raise RuntimeError(GATEWAY_REQUIRED)
    clean = {key: value for key, value in arguments.items() if value is not None}
    clean["route"] = "deal"
    return call_tool(name, clean, timeout=timeout)


def fetch_skills(names: tuple[str, ...]) -> str:
    """Skill markdown from GET {gateway}/skills/{name}. No local file.

    An empty name list (out-of-scope turn) returns "" and does not call the gateway.
    """
    if not names:
        return ""
    base = gateway_base()
    if not base:
        raise RuntimeError(GATEWAY_REQUIRED)
    chunks: list[str] = []
    for name in names:
        response = httpx.get(
            f"{base}/skills/{name}",
            timeout=15.0,
            follow_redirects=False,
        )
        response.raise_for_status()
        text = response.text.strip()
        if not text:
            raise RuntimeError(f"capabilities gateway returned an empty {name} skill")
        chunks.append(f"### skill:{name}\n{text}")
    return "\n\n".join(chunks)
