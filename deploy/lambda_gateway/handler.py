"""AgentCore Gateway Lambda target — same CRE tools as the runtime FastMCP servers."""

from __future__ import annotations

import json
from typing import Any

_ENVELOPE = {"name", "toolName", "id", "context", "toolContext", "arguments", "input", "toolInput"}


def _from_lambda_context(context: Any) -> str:
    cc = getattr(context, "client_context", None)
    custom = getattr(cc, "custom", None) if cc is not None else None
    if isinstance(custom, dict):
        return str(custom.get("bedrockAgentCoreToolName") or custom.get("bedrockAgentCoreToolname") or "")
    return ""


def _payload(event: dict[str, Any], context: Any = None) -> tuple[str, dict[str, Any]]:
    event = event or {}
    name = _from_lambda_context(context)
    if "name" in event and ("arguments" in event or "input" in event):
        name = name or str(event.get("name") or "")
        args = event.get("arguments") or event.get("input") or {}
        return name, dict(args) if isinstance(args, dict) else {}
    ctx = event.get("context") or event.get("toolContext") or {}
    name = name or str(event.get("toolName") or ctx.get("toolName") or event.get("name") or "")
    args = event.get("toolInput") or event.get("input") or event.get("arguments")
    if not isinstance(args, dict):
        args = {k: v for k, v in event.items() if k not in _ENVELOPE}
    return name, args


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    name, args = _payload(event or {}, context)
    short = name.split("___")[-1].split("/")[-1]
    print(
        json.dumps(
            {
                "gateway_lambda": True,
                "name": name,
                "short": short,
                "arg_keys": sorted(args.keys()),
                "event_keys": sorted((event or {}).keys()),
            }
        )
    )
    if short == "cre_ping" or (not short and set(args) <= {"username"}):
        return {"status": "ok", "tool": "cre_ping"}
    from cre_rentroll import turn_ctx
    from cre_rentroll.tools import TOOL_IMPLS

    username = str(args.pop("username", "") or "gateway")
    turn_ctx.reset_turn(username)
    fn = TOOL_IMPLS.get(short) or TOOL_IMPLS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name or short}"}
    result = fn(**args)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"text": result}
    return result if isinstance(result, dict) else {"result": result}
