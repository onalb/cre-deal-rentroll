"""Deal app client for the capabilities gateway.

Capability tools POST {CAPABILITIES_GATEWAY_URL}/mcp/deal. There is no
in-process vault, geocode, search, memo, vacancy, or prediction fallback.
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
        "publish_memo",
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
    base = gateway_base()
    return f"{base}/mcp/deal" if base else ""


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
    """POST {CAPABILITIES_GATEWAY_URL}/mcp/deal tools/call. Does not follow redirects."""
    url = deal_mcp_url()
    if not url:
        raise RuntimeError(GATEWAY_REQUIRED)
    headers = dict(_MCP_HEADERS)
    clean = {key: value for key, value in arguments.items() if value is not None}
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
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
        _post_rpc(client, url, headers, "notifications/initialized", req_id=None)
        result, _ = _post_rpc(
            client,
            url,
            headers,
            "tools/call",
            {"name": name, "arguments": clean},
            req_id=2,
        )
    return _tool_text(result)


def fetch_skills(names: tuple[str, ...]) -> str:
    """Skill markdown from GET {gateway}/skills/{name}."""
    base = gateway_base()
    if not base:
        return ""
    chunks: list[str] = []
    for name in names:
        response = httpx.get(
            f"{base}/skills/{name}",
            timeout=15.0,
            follow_redirects=False,
        )
        response.raise_for_status()
        chunks.append(f"### skill:{name}\n{response.text}")
    return "\n\n".join(chunks)
