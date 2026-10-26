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
    r"query_units|compute_income|compute_noi|compute_cap_rate|ask_vacancy_agent|ask_vacancy|ask_prediction_agent|"
    r"osm_geocode|web_search|create_plan|update_plan(?:_status)?|"
    r"score_gcr|reflection|publish_memo"
)
_PLAN_LINE = re.compile(
    rf"(?im)^\s*\d+\.\s*(?:\[[^\]]+\]\s*)?(?:{_WORK}|publish\s+memo)\b"
)
_CURRENT_STEP = re.compile(r"(?im)^\s*Current step\s*:")
_NEXT_STEPS = re.compile(r"(?im)^\s*Next steps\s*:")
_INVENTORY_LINE = re.compile(
    r"(?i)(no tools \(non-CRE ask\)|full tool menu \(book\)|\[compacted\]|"
    r"\bRUN (inventory|plan|react|mcp)\b)"
)
_REACT_NARRATE = re.compile(
    r"(?i)\b(I will now|Next I will|Now I will proceed|I will proceed|"
    r"Let'?s proceed|"
    r"I will conduct a search|"
    r"calling (?:osm_geocode|query_units))\b"
)
_FAKE_TOOL_JSON = re.compile(
    r"(?is)recipient_name|functions\.publish_memo"
)
_MEMO_ASK_RE = re.compile(
    r"(?is)\b(?:create|publish|write|save|draft|synthesiz\w*)\b.{0,80}\bmemo\b|"
    r"\bmemo\b.{0,80}\b(?:create|publish|write|save|synthesiz\w*|session)\b"
)
_PROCEED_RE = re.compile(
    r"(?i)^(proceed|yes|y|ok|okay|do it|go ahead|continue|publish(?:\s+it)?)\s*[.!]?\s*$"
)
_PLAN_TITLE = re.compile(r"(?im)^\s*Creating the Memo\b.*")
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


def quoted_prediction_clarification(traces: list) -> str:
    """Vacancy MCP not-trained / which-plan text, if the Book called that tool."""
    from cre_rentroll.predict_followup import looks_like_clarification

    for row in reversed(traces or []):
        if str(row.get("name") or "") != "ask_prediction_agent":
            continue
        result = row.get("result")
        if not isinstance(result, dict):
            continue
        answer = str(result.get("answer") or "").strip()
        if answer and looks_like_clarification(answer):
            return answer
    return ""


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


def _publish_memo_params(obj: dict) -> dict[str, str] | None:
    name = str(obj.get("recipient_name") or obj.get("name") or obj.get("tool") or "")
    params = obj.get("parameters") if isinstance(obj.get("parameters"), dict) else None
    if params is None and isinstance(obj.get("arguments"), dict):
        params = obj["arguments"]
    if params is None and {"title", "body"} <= set(obj):
        params = obj
    if "publish_memo" not in name.lower():
        return None
    title = str((params or {}).get("title") or "").strip()
    body = str((params or {}).get("body") or "").strip()
    if title and body:
        return {"title": title, "body": body}
    return None


def leaked_publish_memo_args(text: str) -> dict[str, str] | None:
    """Title+body from dumped functions.publish_memo JSON (not a real tool call)."""
    for _, _, obj in _iter_json_objects(text or ""):
        if isinstance(obj, dict):
            found = _publish_memo_params(obj)
            if found:
                return found
    return None


def is_memo_publish_turn(message: str, history: list | None = None) -> bool:
    """True for create/publish memo, or proceed/yes after a memo ask or memo plan."""
    msg = (message or "").strip()
    if _MEMO_ASK_RE.search(msg):
        return True
    if not _PROCEED_RE.match(msg):
        return False
    for row in reversed(history or []):
        content = str(row.get("content") or "")
        if _MEMO_ASK_RE.search(content):
            return True
        if leaked_publish_memo_args(content):
            return True
        low = content.lower()
        if "publish_memo" in low or "creating the memo" in low or "publish memo" in low:
            return True
    return False


def last_drafted_memo(history: list | None, extra_text: str = "") -> dict[str, str] | None:
    """Last model-written memo (leak JSON or prose). Does not invent a body."""
    rows = list(history or [])
    start = 0
    for i, row in enumerate(rows):
        role = str(row.get("role") or "").strip().lower()
        content = str(row.get("content") or "")
        if role == "user" and _MEMO_ASK_RE.search(content):
            start = i
    window = rows[start:]
    blobs: list[str] = []
    if extra_text and str(extra_text).strip():
        blobs.append(str(extra_text))
    for row in reversed(window):
        if str(row.get("role") or "").strip().lower() != "assistant":
            continue
        kind = str(row.get("kind") or "")
        if kind in {"hitl_pending", "ingest_ack"}:
            continue
        blobs.append(str(row.get("content") or ""))
    for blob in blobs:
        leaked = leaked_publish_memo_args(blob)
        if leaked:
            return leaked
    canned = "saving a memo needs your approval"
    for blob in blobs:
        text = strip_operator_echo(blob)
        if not text or is_operator_leak(text):
            continue
        if canned in text.lower():
            continue
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue
        title = lines[0][:200]
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else text.strip()
        if len(body) < 40:
            continue
        if title == body:
            title = "memo"
        return {"title": title, "body": body}
    return None


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
    if _PLAN_TITLE.search(text):
        return True
    if _STATUS_TAG.search(text):
        return True
    if _FAKE_TOOL_JSON.search(text) or leaked_publish_memo_args(text):
        return True
    return _looks_like_tool_json(text)


def _strip_leaked_tool_json(text: str) -> str:
    blob = text or ""
    chunks: list[str] = []
    last = 0
    found = False
    for start, end, obj in _iter_json_objects(blob):
        if not isinstance(obj, dict):
            continue
        name = str(obj.get("recipient_name") or obj.get("name") or "")
        if _publish_memo_params(obj) or "recipient_name" in obj or "functions." in name:
            found = True
            chunks.append(blob[last:start])
            last = end
    if not found:
        return blob
    chunks.append(blob[last:])
    return re.sub(r"\n{3,}", "\n\n", "".join(chunks)).strip()


def strip_operator_echo(draft: str) -> str:
    """Drop checklist / Current step / tool JSON / score_gcr JSON. Do not invent an answer."""
    text = _strip_leaked_tool_json(strip_eval_json(draft))
    kept: list[str] = []
    for line in text.splitlines():
        if _PLAN_LINE.match(line) or _CURRENT_STEP.match(line) or _NEXT_STEPS.match(line):
            continue
        if _INVENTORY_LINE.search(line) or _OPERATOR_LEAK.match(line.strip()):
            continue
        if _STATUS_TAG.search(line) or _REACT_NARRATE.search(line) or _PLAN_TITLE.match(line):
            continue
        kept.append(line)
    text = "\n".join(kept).strip()
    if _looks_like_tool_json(text) or _looks_like_eval_json(text):
        return ""
    if _REACT_NARRATE.search(text) or _STATUS_TAG.search(text) or _PLAN_TITLE.search(text):
        return ""
    return text


def last_scored_prose(traces: list | None) -> str:
    """Last score_gcr draft that scored ok. Failed eval drafts are not user-facing."""
    for row in reversed(traces or []):
        if str((row or {}).get("name") or "") != "score_gcr":
            continue
        result = (row or {}).get("result") or {}
        decision = ""
        if isinstance(result, dict):
            decision = str(result.get("decision") or "")
        if decision != "ok":
            return ""
        args = (row or {}).get("args") or {}
        text = strip_operator_echo(answer_prose(str(args.get("draft") or "")))
        if text and not is_operator_leak(text):
            return text
        return ""
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
