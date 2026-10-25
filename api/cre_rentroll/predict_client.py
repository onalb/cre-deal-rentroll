"""MCP client for the vacancy prediction agent on localhost:8000.

Rentroll does not bind 8000 and never uploads or trains. Training is only
via the vacancy-agent UI (file picker + Send rent roll). External callers
use streamable-HTTP MCP at {RR_PREDICT_URL}/mcp tool ask_vacancy. The
vacancy UI may still use SSE POST /api/chat; Rentroll must not.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

import httpx

_OCCUPANCY_ONLY_RE = re.compile(
    r"^what occupancy do you predict(?:\s+for\s+.+?)?(?:\s+as of\s+.+?)?\??$",
    re.I,
)
_CLARIFY_RE = re.compile(
    r"(?is)("
    r"not trained|no (?:saved )?fit|not saved|"
    r"which (?:floor )?plan|which group|which variant|"
    r"which .{0,60}renovat|"
    r"ambiguous_plan|"
    r"which properties (?:are|have been) trained|"
    r"have not been trained"
    r")"
)
_NEW_PREDICT_RE = re.compile(
    r"(?is)\b("
    r"what occupancy do you predict|"
    r"over the next\s+\d+\s+months|"
    r"predicted occupancy|"
    r"forecast occupancy"
    r")\b"
)

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


def looks_like_clarification(text: str) -> bool:
    """True when vacancy MCP asked a follow-up or said the building has no fit."""
    return bool(_CLARIFY_RE.search((text or "").strip()))


def _generic_property_label(name: str) -> bool:
    n = (name or "").strip().lower()
    return not n or n in {"this property", "the property", "this building", "the building"}


def user_named_property(question: str, property_name: str = "") -> bool:
    """True when THIS question already contains the given property name."""
    asked = (question or "").strip()
    name = (property_name or "").strip()
    if not asked or _generic_property_label(name):
        return False
    return name.lower() in asked.lower()


def this_turn_clarify_result() -> dict[str, Any] | None:
    """First ask_prediction_agent clarification already recorded this user turn."""
    try:
        from cre_rentroll import turn_ctx

        traces = turn_ctx.traces()
    except Exception:
        return None
    for row in traces or []:
        if str(row.get("name") or "") != "ask_prediction_agent":
            continue
        result = row.get("result")
        if not isinstance(result, dict):
            continue
        answer = str(result.get("answer") or "").strip()
        if answer and looks_like_clarification(answer):
            return result
    return None


def is_followup_reply(message: str) -> bool:
    """True for a short plan/variant reply, not a new occupancy/forecast ask."""
    asked = (message or "").strip()
    if not asked:
        return False
    if _OCCUPANCY_ONLY_RE.match(asked):
        return False
    if _NEW_PREDICT_RE.search(asked) and len(asked) > 48:
        return False
    return True


def session_awaiting_followup(username: str) -> bool:
    row = _state.get(username or "")
    return bool(row and row.get("awaiting_followup"))


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


def vault_facts(roll: Any, as_of: str | None = None) -> dict[str, Any]:
    """Book facts for the MCP question. Never a file upload."""
    as_of = as_of or getattr(roll, "as_of", None)
    plans: list[str] = []
    seen: set[str] = set()
    for unit in getattr(roll, "units", None) or []:
        name = (getattr(unit, "unit_type", None) or "").strip()
        if name and name not in seen:
            seen.add(name)
            plans.append(name)
    property_name = (
        (getattr(roll, "property_name", None) or "").strip()
        or (getattr(roll, "source_filename", None) or "").strip()
        or "this property"
    )
    unit_count = int(getattr(roll, "unit_count", 0) or 0)
    occ_fn = getattr(roll, "occupancy_pct_on", None)
    vacant_fn = getattr(roll, "vacant_count_on", None)
    occupancy_pct = (
        round(float(occ_fn(as_of)), 4) if callable(occ_fn) and unit_count else 0.0
    )
    vacant_count = int(vacant_fn(as_of)) if callable(vacant_fn) else 0
    return {
        "property_name": property_name,
        "as_of": as_of or "",
        "unit_count": unit_count,
        "occupancy_pct": occupancy_pct,
        "vacant_count": vacant_count,
        "floor_plans": plans,
    }


def build_forecast_question(
    question: str = "",
    horizon_months: int = 12,
    vault: dict[str, Any] | None = None,
) -> str:
    """MCP ask_vacancy question: user text + do-not-parse. Never invent a building."""
    vault = vault or {}
    name = str(vault.get("property_name") or "").strip()
    as_of = str(vault.get("as_of") or "").strip()
    asked = (question or "").strip()
    named = user_named_property(asked, name)
    occupancy_only = bool(asked) and bool(_OCCUPANCY_ONLY_RE.match(asked))
    if occupancy_only:
        asked = asked or "What occupancy do you predict?"
    else:
        if not asked:
            asked = (
                f"Over the next {horizon_months} months, how many units expire or are MTM, "
                "what monthly income is at risk, and what occupancy do you predict"
                + (f" for {name}" if named else "")
                + "?"
            )
        if "do not request a rent roll" not in asked.lower():
            asked = asked.rstrip(" ?") + "? Do not request a rent roll file."
    if "do not parse" not in asked.lower() and "do not request a rent roll" not in asked.lower():
        asked = asked.rstrip(" ?") + "? Do not parse or train."
    lines = [
        "Do not parse, upload, or train. Do not request a rent roll file. "
        "This caller is questions-only (MCP ask_vacancy). A fit may already exist."
    ]
    if named:
        headed = f"Score occupancy for {name}"
        if as_of:
            headed += f" as of {as_of}"
        lines.append(headed + ".")
        bits = [
            f"unit_count={vault.get('unit_count')}",
            f"occupancy_pct={vault.get('occupancy_pct')}",
            f"vacant_count={vault.get('vacant_count')}",
        ]
        lines.append("Book snapshot for context only (not a file to parse): " + "; ".join(bits) + ".")
    else:
        lines.append(
            "If no building is named in the question, do not assume a vault or trained "
            "property. load_fit(\"\") and say which properties are trained, or ask which "
            "building to score."
        )
    lines.append("Question: " + asked)
    return "\n".join(lines)


def forecast(
    username: str,
    question: str,
    horizon_months: int = 12,
    vault: dict[str, Any] | None = None,
) -> dict:
    prior = this_turn_clarify_result()
    if prior:
        answer = str(prior.get("answer") or "").strip()
        return {
            "source": "vacancy-prediction-agent",
            "url": predict_base_url(),
            "horizon_months": horizon_months,
            "answer": answer,
            "ok": True,
            "same_turn_stop": True,
        }
    state = _session(username)
    asked = (question or "").strip()
    followup = bool(state.get("awaiting_followup")) and is_followup_reply(asked)
    if followup:
        prompt = asked
    else:
        prompt = build_forecast_question(
            question, horizon_months=horizon_months, vault=vault
        )
    result = chat(username, prompt)
    answer = result["message"]
    state["awaiting_followup"] = looks_like_clarification(answer)
    return {
        "source": "vacancy-prediction-agent",
        "url": predict_base_url(),
        "horizon_months": horizon_months,
        "answer": answer,
        "ok": True,
    }
