"""Sign one AgentCore MCP call as this app's identity role.

tools/list is cached for the current turn. tools/call is one request per tool.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx

_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-03-26",
}

_DEAL_ROLE = "arn:aws:iam::226721156429:role/CreDealRentrollWli"
_creds: dict[str, Any] | None = None
_creds_until = 0.0
_initialized = False
_menu: list[str] | None = None


def mcp_url() -> str:
    raw = (
        os.environ.get("MCP_URL")
        or os.environ.get("AGENTCORE_GATEWAY_URL")
        or ""
    ).strip().rstrip("/")
    if raw and not raw.endswith("/mcp"):
        parsed = urlparse(raw)
        if parsed.path in {"", "/"}:
            raw = raw + "/mcp"
    return raw


def reset_menu() -> None:
    global _menu
    _menu = None


def _role_arn() -> str:
    return (os.environ.get("MCP_CALLER_ROLE_ARN") or _DEAL_ROLE).strip()


def _credentials():
    global _creds, _creds_until
    now = time.time()
    if _creds is not None and now < _creds_until:
        return _creds
    import boto3

    assumed = boto3.client("sts", region_name=os.environ.get("AWS_REGION") or "us-east-1").assume_role(
        RoleArn=_role_arn(),
        RoleSessionName="deal-mcp",
    )
    _creds = assumed["Credentials"]
    _creds_until = now + 600
    return _creds


def _sign_and_post(body: dict[str, Any], timeout: float) -> dict[str, Any]:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    url = mcp_url()
    if not url:
        raise RuntimeError("MCP_URL is not set")
    raw = _credentials()
    creds = Credentials(raw["AccessKeyId"], raw["SecretAccessKey"], raw["SessionToken"])
    data = json.dumps(body).encode("utf-8")
    request = AWSRequest(method="POST", url=url, data=data, headers=dict(_MCP_HEADERS))
    SigV4Auth(creds, "bedrock-agentcore", os.environ.get("AWS_REGION") or "us-east-1").add_auth(request)
    prepared = request.prepare()
    response = httpx.post(
        url,
        content=prepared.body,
        headers=dict(prepared.headers),
        timeout=timeout,
        follow_redirects=False,
    )
    if response.status_code in {301, 302, 303, 307, 308}:
        raise RuntimeError(f"MCP URL redirected ({response.status_code}); refusing to follow")
    if response.status_code >= 400:
        raise RuntimeError(f"MCP HTTP {response.status_code}: {response.text[:300]}")
    text = (response.text or "").strip()
    if not text:
        return {}
    if text.startswith("event:") or "data:" in text.splitlines()[0]:
        for line in text.splitlines():
            if line.startswith("data:"):
                text = line[5:].strip()
                break
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise RuntimeError("MCP returned a non-object response")
    if parsed.get("error"):
        err = parsed["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(message or "MCP error")
    return parsed


def _ensure_session(timeout: float) -> None:
    global _initialized
    if _initialized:
        return
    _sign_and_post(
        {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "cre-deal-rentroll", "version": "0.1.0"},
            },
        },
        timeout,
    )
    _sign_and_post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout)
    _initialized = True


def list_tools(timeout: float = 30.0) -> list[str]:
    """Tool names for this turn. A second call in the same turn reuses the list."""
    global _menu
    if _menu is not None:
        return list(_menu)
    _ensure_session(timeout)
    data = _sign_and_post(
        {"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}},
        timeout,
    )
    result = data.get("result") or {}
    tools = result.get("tools") or []
    names: list[str] = []
    for item in tools:
        if isinstance(item, dict) and item.get("name"):
            raw = str(item["name"])
            names.append(raw.split("___")[-1])
    _menu = names
    return list(names)


def call_tool(name: str, arguments: dict[str, Any], timeout: float = 180.0) -> str:
    _ensure_session(timeout)
    remote = f"cretools___{name}"
    data = _sign_and_post(
        {
            "jsonrpc": "2.0",
            "id": "call",
            "method": "tools/call",
            "params": {"name": remote, "arguments": arguments},
        },
        timeout,
    )
    result = data.get("result") or {}
    if result.get("isError"):
        content = result.get("content")
        text = ""
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = str(content[0].get("text") or "")
        raise RuntimeError(text or "MCP tool error")
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        text = content[0].get("text")
        if text:
            return str(text)
    structured = result.get("structuredContent")
    if isinstance(structured, str):
        return structured
    if isinstance(structured, dict):
        return json.dumps(structured)
    return json.dumps(result)
