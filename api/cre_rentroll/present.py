from __future__ import annotations

import json
import re

_RETRY = re.compile(r"\n*\[eval retry:.*?\]\s*$", re.I | re.S)
_EVAL_BANNER = re.compile(r"\n*—\s*Warning:\s*live eval[\s\S]*$", re.I)
_OPERATOR_LEAK = re.compile(
    r"(?is)^\s*(Need replan:|Plan replaced|Plan updated|Plan unchanged|"
    r"Checklist already exists|Reflection stored|No checklist yet|"
    r"Search already failed)"
)
_WORK = (
    r"query_units|compute_income|compute_noi|compute_cap_rate|"
    r"osm_geocode|web_search|create_plan|update_plan(?:_status)?|"
    r"score_gcr|reflection|publish_memo"
)
_PLAN_LINE = re.compile(rf"(?im)^\s*\d+\.\s*(?:\[[^\]]+\]\s*)?(?:{_WORK})\b")
_CURRENT_STEP = re.compile(r"(?im)^\s*Current step\s*:")
_NEXT_STEPS = re.compile(r"(?im)^\s*Next steps\s*:")
_INVENTORY_LINE = re.compile(
    r"(?i)(no tools \(non-CRE ask\)|full tool menu \(book\)|\[compacted\]|"
    r"\bRUN (inventory|plan|react|mcp)\b)"
)
_REACT_NARRATE = re.compile(
    r"(?i)\b(I will now|Next I will|I will conduct a search|"
    r"calling (?:osm_geocode|query_units))\b"
)
_STATUS_TAG = re.compile(
    r"(?i)\[(?:tag:\s*)?(?:in_progress|pending|completed|incomplete)\]"
)
_TOOL_JSON_KEYS = frozenset(
    {
        "unit_count",
        "occupancy_pct",
        "vacant_count",
        "occupied_count",
        "in_place_monthly",
        "mtm_count",
        "display_name",
        "osm_id",
        "implied_cap",
    }
)
_EVAL_JSON_MARKERS = frozenset(
    {
        "request_C",
        "vault_calc",
        "vault_calc_ok",
        "vault_invented",
        "judge",
        "decision",
        "hint",
    }
)


def user_visible(raw: str) -> str:
    s = _RETRY.sub("", raw or "")
    return s.strip() or (raw or "").strip()


def _is_eval_obj(obj: object) -> bool:
    if not isinstance(obj, dict):
        return False
    keys = set(obj)
    if keys & _EVAL_JSON_MARKERS and ({"G", "R", "request_C", "judge", "decision"} & keys):
        return True
    return {"G", "R", "request_C"} <= keys and "judge" in keys


def _iter_json_objects(text: str):
    dec = json.JSONDecoder()
    i = 0
    n = len(text or "")
    while i < n:
        start = text.find("{", i)
        if start < 0:
            return
        try:
            obj, end = dec.raw_decode(text, start)
        except json.JSONDecodeError:
            i = start + 1
            continue
        yield start, end, obj
        i = end


def strip_eval_json(text: str) -> str:
    """Drop score_gcr / live-judge objects. Keep surrounding prose."""
    blob = text or ""
    chunks: list[str] = []
    last = 0
    found = False
    for start, end, obj in _iter_json_objects(blob):
        if not _is_eval_obj(obj):
            continue
        found = True
        chunks.append(blob[last:start])
        last = end
    if not found:
        return blob.strip()
    chunks.append(blob[last:])
    return re.sub(r"\n{3,}", "\n\n", "".join(chunks)).strip()


def _parse_json_obj(text: str) -> dict | None:
    blob = (text or "").strip()
    if not blob.startswith("{") or "}" not in blob:
        return None
    try:
        obj = json.loads(blob)
    except (TypeError, ValueError):
        start = blob.find("{")
        end = blob.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(blob[start : end + 1])
        except (TypeError, ValueError):
            return None
    return obj if isinstance(obj, dict) else None


def _looks_like_eval_json(text: str) -> bool:
    obj = _parse_json_obj(text)
    if obj is not None and _is_eval_obj(obj):
        return True
    leftover = strip_eval_json(text)
    return bool((text or "").strip()) and leftover == "" and any(
        _is_eval_obj(obj) for _, _, obj in _iter_json_objects(text or "")
    )


def _looks_like_tool_json(text: str) -> bool:
    if _looks_like_eval_json(text):
        return True
    obj = _parse_json_obj(text)
    if obj is None:
        return False
    keys = set(obj)
    if keys & _TOOL_JSON_KEYS:
        return True
    if {"lat", "lon"} <= keys and ({"ok", "display_name", "osm_id"} & keys):
        return True
    return False


def is_operator_leak(draft: str) -> bool:
    """True when the draft is planner / tool-trace / inventory / eval JSON, not a user sentence."""
    text = (draft or "").strip()
    if not text:
        return False
    if _looks_like_eval_json(text):
        return True
    if _OPERATOR_LEAK.match(text):
        return True
    low = text.lower()
    if "call update_plan" in low and "new remaining work" in low:
        return True
    if _PLAN_LINE.search(text) or _CURRENT_STEP.search(text) or _NEXT_STEPS.search(text):
        return True
    if _INVENTORY_LINE.search(text) or _REACT_NARRATE.search(text):
        return True
    if _STATUS_TAG.search(text):
        return True
    return _looks_like_tool_json(text)


def strip_operator_echo(draft: str) -> str:
    """Drop checklist / Current step / tool JSON / score_gcr JSON. Do not invent an answer."""
    text = strip_eval_json(draft)
    kept: list[str] = []
    for line in text.splitlines():
        if _PLAN_LINE.match(line) or _CURRENT_STEP.match(line) or _NEXT_STEPS.match(line):
            continue
        if _INVENTORY_LINE.search(line) or _OPERATOR_LEAK.match(line.strip()):
            continue
        if _STATUS_TAG.search(line) or _REACT_NARRATE.search(line):
            continue
        kept.append(line)
    text = "\n".join(kept).strip()
    if _looks_like_tool_json(text) or _looks_like_eval_json(text):
        return ""
    if _REACT_NARRATE.search(text) or _STATUS_TAG.search(text):
        return ""
    return text


def last_scored_prose(traces: list | None) -> str:
    """Last score_gcr draft that is a user sentence, not Need replan / checklist / eval JSON."""
    for row in reversed(traces or []):
        if str((row or {}).get("name") or "") != "score_gcr":
            continue
        args = (row or {}).get("args") or {}
        text = strip_operator_echo(answer_prose(str(args.get("draft") or "")))
        if text and not is_operator_leak(text):
            return text
    return ""


def answer_prose(draft: str, as_of: str | None = None, question: str | None = None) -> str:
    """Chat text is the agent draft. Strip leftover eval banners only."""
    _ = as_of, question
    return _EVAL_BANNER.sub("", draft or "").strip()


def scope_fail_chat(detail: str) -> str:
    d = (detail or "").lower()
    if "429" in d or "rate" in d:
        return "The model is busy right now. Wait a minute and send again."
    if "key" in d or "unauthorized" in d or "missing" in d:
        return "The app cannot reach the model right now. Check the API key and try again."
    if "timeout" in d or "network" in d:
        return "That request timed out. Wait a moment and send again."
    return "I could not start this question. Wait a moment and send again."
