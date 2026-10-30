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


def _agent_dbg(hid: str, loc: str, msg: str, extra: dict[str, Any] | None = None) -> None:
    # #region agent log
    raw = os.environ.get("CAPABILITIES_GATEWAY_URL")
    mcp = os.environ.get("MCP_URL") or os.environ.get("AGENTCORE_GATEWAY_URL")
    data: dict[str, Any] = {
        "gw_is_none": raw is None,
        "gw_len": len((raw or "").strip()),
        "gw_prefix": (raw or "").strip()[:24],
        "mcp_len": len((mcp or "").strip()),
        "inside": os.environ.get("AGENTCORE_INSIDE", ""),
        "n_environ": len(os.environ),
        "keys": sorted(
            k
            for k in os.environ
            if any(s in k.upper() for s in ("CAPABILIT", "MCP_URL", "GATEWAY", "AGENTCORE_INSIDE"))
        ),
    }
    if extra:
        data.update(extra)
    payload = {
        "sessionId": "3cdd74",
        "runId": "pre-fix",
        "hypothesisId": hid,
        "location": loc,
        "message": msg,
        "data": data,
        "timestamp": int(__import__("time").time() * 1000),
    }
    line = json.dumps(payload, default=str)
    for path in (
        r"C:\Users\burak\cre-deal-rentroll\debug-3cdd74.log",
        "/app/debug-3cdd74.log",
        "debug-3cdd74.log",
    ):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            break
        except Exception:
            continue
    try:
        from cre_rentroll.run_events import emit

        emit("debug", f"{hid} {msg} gw_len={data['gw_len']} mcp_len={data['mcp_len']} inside={data['inside']!r} keys={data['keys']}")
    except Exception:
        pass
    headers = {"Content-Type": "application/json", "X-Debug-Session-Id": "3cdd74"}
    for url in (
        "http://127.0.0.1:7663/ingest/ef167736-0a3f-4c8a-b991-a07eb353bc2f",
        "http://host.docker.internal:7663/ingest/ef167736-0a3f-4c8a-b991-a07eb353bc2f",
    ):
        try:
            httpx.post(url, json=payload, headers=headers, timeout=0.4)
            break
        except Exception:
            continue
    # #endregion


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
        _agent_dbg("D", "capabilities_client.py:call_deal_tool", "mcp_url empty", {"tool": name})
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
    _agent_dbg("A", "capabilities_client.py:fetch_skills", "fetch_skills base", {"n_names": len(names), "has_base": bool(base)})
    if not base:
        _agent_dbg("B", "capabilities_client.py:fetch_skills", "GATEWAY_REQUIRED empty base", {})
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
