from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from cre_rentroll import turn_ctx, vault
from cre_rentroll.schemas import CanonicalRentRoll

NOMINATIM = "https://nominatim.openstreetmap.org/search"

_TASK_STATUSES = ("completed", "incomplete", "in_progress", "pending")
_NUM_PREFIX = re.compile(r"^\s*\d+[.)\-:]\s*")
# Same markers mid-line so "1. foo. 2. bar" is two items, not one blob.
_ITEM_BREAK = re.compile(r"(?:^|\s+)(?=\d+[.)\-:]\s+\S)")
_BULLET_PREFIX = re.compile(r"^[-*+]\s+")
_LEAD_TAG = re.compile(r"^(?:\[(?P<br>[^\]]*)\]|\*\*(?P<bd>[^*]+)\*\*)\s*", re.I)
_ANY_TAG = re.compile(r"\[(?P<br>[^\]]*)\]|\*\*(?P<bd>[^*]+)\*\*", re.I)
_TRAIL_WORD = re.compile(
    r"[\s:;(—–-]+(?P<w>completed|complete|done|finished|incomplete|incomplet|"
    r"cannot_complete|unanswerable|unavailable|blocked|"
    r"in[_-]?progress|pending|todo)\s*$",
    re.I,
)
_STATUS_MAP = {
    "completed": "completed",
    "complete": "completed",
    "done": "completed",
    "finished": "completed",
    "checked": "completed",
    "x": "completed",
    "✓": "completed",
    "✔": "completed",
    "✅": "completed",
    "in_progress": "in_progress",
    "in-progress": "in_progress",
    "inprogress": "in_progress",
    "doing": "in_progress",
    "current": "in_progress",
    "wip": "in_progress",
    "working": "in_progress",
    "pending": "pending",
    "todo": "pending",
    "open": "pending",
    "unchecked": "pending",
    "incomplete": "incomplete",
    "incomplet": "incomplete",
    "cannot_complete": "incomplete",
    "unanswerable": "incomplete",
    "unavailable": "incomplete",
    "blocked": "incomplete",
    "n/a": "incomplete",
    "na": "incomplete",
    "": "pending",
}


VAULT_TOOL_NAMES = frozenset(
    {"query_units", "compute_income", "compute_noi", "compute_cap_rate"}
)
WORK_TOOL_NAMES = frozenset(
    {
        *VAULT_TOOL_NAMES,
        "osm_geocode",
        "web_search",
        "publish_memo",
    }
)
_HEADLINE_COUNT_RE = re.compile(
    r"\b(unit_count|unit count|occupied_count|vacant_count|occupancy_pct|"
    r"occupancy|occupied|vacant|units|mtm_count|mtm|month[\s-]*to[\s-]*month)\b",
    re.I,
)
_LIST_UNITS_RE = re.compile(
    r"\b(list|enumerat\w*|unit rows|unit ids?|row-by-row|names of)\b",
    re.I,
)
_NON_HEADLINE_TOOLS_RE = re.compile(
    r"\b(compute_income|compute_noi|compute_cap_rate|osm_geocode|web_search|"
    r"publish_memo|score_gcr|reflection)\b",
    re.I,
)
# Checklist prose → vault tool (exact snake_case names are matched first).
_INCOME_ITEM_RE = re.compile(
    r"\b(compute[\s_-]*income|in[\s_-]*place|monthly)\b",
    re.I,
)
_QUERY_UNITS_PHRASE_RE = re.compile(r"\bquery[\s_-]*units\b", re.I)
HEADLINE_COUNTS_NOTE = (
    "These headline counts are complete in this result; another query_units is "
    "only needed to LIST a filtered subset of unit rows, not to re-fetch counts."
)


def _roll() -> CanonicalRentRoll:
    user = turn_ctx.username() or os.environ.get("CRE_RENTROLL_USER")
    if not user:
        raise RuntimeError("no username in turn context")
    roll = vault.load_roll(user)
    if roll is None:
        raise RuntimeError("no rent roll in the session vault — attach a CSV/xlsx first")
    return roll


def _need_file_json(name: str, args: dict[str, Any]) -> str:
    """Python vault gate — no extra LLM. Empty vault cannot run compute/query."""
    from cre_rentroll.scope import NEED_FILE_TEXT

    payload = {"need_file": True, "ok": True, "message": NEED_FILE_TEXT}
    turn_ctx.record(name, args, payload)
    return json.dumps(payload)


def _is_empty_vault_error(exc: BaseException) -> bool:
    return "no rent roll" in str(exc).lower()


def _norm_task_text(raw: str) -> str:
    s = re.sub(r"\s+", " ", str(raw or "").strip()).strip(" -:;—–,")
    s = re.sub(r"\s*\(\s*MCP\s*\)", "", s, flags=re.I)
    return s.strip(" -:;—–,")


def _token_status(token: str) -> str | None:
    t = str(token or "").strip().lower()
    if t in {"x", "✓", "✔", "✅"}:
        return "completed"
    key = re.sub(r"[\s\-]+", "_", t)
    if key in _STATUS_MAP:
        return _STATUS_MAP[key]
    return _STATUS_MAP.get(key.replace("_", ""))


def _coerce_status(value: Any) -> str:
    return _token_status(str(value or "")) or "pending"


def _split_status(text: str) -> tuple[str | None, str]:
    s = str(text or "").strip()
    found: str | None = None
    lead = _LEAD_TAG.match(s)
    if lead:
        tok = lead.group("br") if lead.group("br") is not None else lead.group("bd")
        st = _token_status(tok if tok is not None else "")
        if st:
            found = st
            s = s[lead.end() :].strip()

    def _keep_or_strip(m: re.Match[str]) -> str:
        nonlocal found
        tok = m.group("br") if m.group("br") is not None else m.group("bd")
        st = _token_status(tok if tok is not None else "")
        if not st:
            return m.group(0)
        if found is None:
            found = st
        return " "

    s = _ANY_TAG.sub(_keep_or_strip, s)
    if found is None:
        trail = _TRAIL_WORD.search(s)
        if trail:
            found = _token_status(trail.group("w"))
            s = s[: trail.start()].strip()
    return found, _norm_task_text(s)


def _task_item(text: Any, status: Any = None) -> dict[str, str] | None:
    raw = str(text or "").strip()
    stripped = _NUM_PREFIX.sub("", raw)
    stripped = _BULLET_PREFIX.sub("", stripped)
    tagged, body = _split_status(stripped)
    if not body:
        return None
    explicit = None
    if status is not None and str(status).strip() != "":
        explicit = _token_status(str(status))
    chosen = explicit or tagged
    return {"text": body, "status": chosen if chosen in _TASK_STATUSES else "pending"}


def _split_joined_tool_blob(blob: str) -> str:
    """Turn 'osm_geocode, query_units' into two lines so each tool is its own task."""
    names = sorted(WORK_TOOL_NAMES, key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in names)
    mcp = r"(?:\s*\(\s*MCP\s*\))?"
    tagged = r"(?:\s*\[[^\]]+\])?"
    return re.sub(
        rf"((?:{alt}){mcp}{tagged})\s*(?:,|/|;|\band\b|\bthen\b|\+)\s*(?=(?:{alt}))",
        r"\1\n",
        str(blob or ""),
        flags=re.I,
    )


def _explode_task_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for it in items:
        blob = _split_joined_tool_blob(str(it.get("text") or ""))
        parts = [p for p in (_task_item(ln) for ln in _task_lines(blob)) if p]
        if len(parts) <= 1:
            out.append(it)
            continue
        for i, part in enumerate(parts):
            if i == 0 and it.get("status") in _TASK_STATUSES and part["status"] == "pending":
                part = {**part, "status": it["status"]}
            out.append(part)
    return out


def _task_lines(blob: str) -> list[str]:
    """Split a tasks blob on newlines and on numbered markers (`1.`, `2)`)."""
    out: list[str] = []
    for ln in str(blob or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        parts = [p.strip() for p in _ITEM_BREAK.split(ln) if p and p.strip()]
        out.extend(parts or [ln])
    return out


def parse_task_items(tasks: Any) -> list[dict[str, str]]:
    if isinstance(tasks, dict) and "tasks" in tasks:
        return parse_task_items(tasks.get("tasks"))
    if isinstance(tasks, (list, tuple)):
        items: list[dict[str, str]] = []
        for row in tasks:
            if isinstance(row, dict):
                item = _task_item(
                    row.get("text") or row.get("task") or row.get("title") or "",
                    row.get("status"),
                )
            else:
                item = _task_item(row)
            if item:
                items.append(item)
        return items
    blob = str(tasks or "").strip()
    if blob.startswith("[") or blob.startswith("{"):
        try:
            return parse_task_items(json.loads(blob))
        except json.JSONDecodeError:
            pass
    blob = _split_joined_tool_blob(blob)
    items = [item for item in (_task_item(ln) for ln in _task_lines(blob)) if item]
    return _explode_task_items(items)


def format_checklist(items: list[dict[str, str]]) -> str:
    if not items:
        return ""
    return "\n".join(f"{i}. [{it['status']}] {it['text']}" for i, it in enumerate(items, 1))


def _fingerprint(items: list[dict[str, str]]) -> list[tuple[str, str]]:
    return [(it["text"], it["status"]) for it in items]


def checklist_diff(previous: list[dict[str, str]], current: list[dict[str, str]]) -> dict[str, Any]:
    prev_map = {it["text"]: it for it in previous}
    newly_completed = [
        it["text"]
        for it in current
        if it["status"] == "completed" and (prev_map.get(it["text"]) or {}).get("status") != "completed"
    ]
    newly_incomplete = [
        it["text"]
        for it in current
        if it["status"] == "incomplete"
        and (prev_map.get(it["text"]) or {}).get("status") != "incomplete"
    ]
    new_items = [it["text"] for it in current if it["text"] not in prev_map]
    return {
        "initial": not previous,
        "changed": _fingerprint(previous) != _fingerprint(current),
        "completed": newly_completed,
        "incomplete": newly_incomplete,
        "new": new_items,
        "in_progress": [it["text"] for it in current if it["status"] == "in_progress"],
        "in_progress_was": [it["text"] for it in previous if it["status"] == "in_progress"],
    }


def _task_key(text: str) -> str:
    return _norm_task_text(text).lower()


def _is_search_task(text: str) -> bool:
    low = (text or "").lower()
    if "web_search" in low:
        return True
    return bool(re.search(r"\bsearch\b", low))


def web_search_failed(traces: list[dict[str, Any]] | None = None) -> bool:
    rows = traces if traces is not None else turn_ctx.traces()
    for row in rows or []:
        if row.get("name") != "web_search":
            continue
        result = row.get("result")
        if isinstance(result, dict) and result.get("ok") is False:
            return True
        blob = str(result or "").lower()
        if "forced failure" in blob or "search-down" in blob:
            return True
    return False


def _mark_search_incomplete(previous: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for it in previous:
        if it.get("status") not in _TERMINAL_STATUSES and _is_search_task(it.get("text") or ""):
            out.append({"text": it["text"], "status": "incomplete"})
        else:
            out.append({"text": it["text"], "status": it["status"]})
    return out


_TERMINAL_STATUSES = frozenset({"completed", "incomplete"})


def merge_replan_checklist(
    previous: list[dict[str, str]], incoming: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Keep completed/incomplete rows; append new work. Do not reset done items."""
    kept = [
        {"text": it["text"], "status": it["status"]}
        for it in previous
        if it.get("status") in _TERMINAL_STATUSES
    ]
    seen = {_task_key(it["text"]) for it in kept}
    added: list[dict[str, str]] = []
    for it in incoming:
        key = _task_key(it["text"])
        if not key or key in seen:
            continue
        status = it.get("status") or "pending"
        if status in _TERMINAL_STATUSES:
            status = "pending"
        added.append({"text": it["text"], "status": status})
        seen.add(key)
    return kept + added


def apply_status_updates(
    previous: list[dict[str, str]], incoming: list[dict[str, str]]
) -> tuple[list[dict[str, str]], int]:
    """Keep original items; apply status only where incoming text matches.

    Extra or wholly new incoming lines are ignored. One updatable list.
    """
    incoming_by_key: dict[str, dict[str, str]] = {}
    for it in incoming:
        incoming_by_key[_task_key(it["text"])] = it
    matched = 0
    result: list[dict[str, str]] = []
    for it in previous:
        key = _task_key(it["text"])
        hit = incoming_by_key.get(key)
        if hit:
            matched += 1
            result.append({"text": it["text"], "status": hit["status"]})
        else:
            result.append({"text": it["text"], "status": it["status"]})
    return result, matched


def _text_has_status_tag(raw: str) -> bool:
    stripped = _BULLET_PREFIX.sub("", _NUM_PREFIX.sub("", str(raw or "").strip()))
    tagged, _ = _split_status(stripped)
    return tagged is not None


def _tasks_omit_status_tags(tasks: Any) -> bool:
    if isinstance(tasks, dict) and "tasks" in tasks:
        if str(tasks.get("status") or "").strip():
            return False
        return _tasks_omit_status_tags(tasks.get("tasks"))
    if isinstance(tasks, (list, tuple)):
        if not tasks:
            return False
        for row in tasks:
            if isinstance(row, dict):
                if str(row.get("status") or "").strip():
                    return False
                blob = str(row.get("text") or row.get("task") or row.get("title") or "")
                if _text_has_status_tag(blob):
                    return False
            elif _text_has_status_tag(str(row)):
                return False
        return True
    blob = str(tasks or "").strip()
    if not blob:
        return False
    if blob.startswith("[") or blob.startswith("{"):
        try:
            return _tasks_omit_status_tags(json.loads(blob))
        except json.JSONDecodeError:
            pass
    blob = _split_joined_tool_blob(blob)
    lines = _task_lines(blob)
    if not lines:
        return False
    return not any(_text_has_status_tag(ln) for ln in lines)


def _work_tools_ran_ok() -> set[str]:
    names: set[str] = set()
    for row in turn_ctx.traces() or []:
        name = str(row.get("name") or "")
        if name not in WORK_TOOL_NAMES:
            continue
        result = row.get("result")
        if isinstance(result, dict) and result.get("ok") is False:
            continue
        names.add(name)
    return names


def _consecutive_status_without_work() -> bool:
    for row in reversed(turn_ctx.traces() or []):
        name = str(row.get("name") or "")
        if name in WORK_TOOL_NAMES:
            return False
        if name == "update_plan_status":
            return True
    return False


def _next_status_hint(items: list[dict[str, str]]) -> str:
    for it in items:
        if it.get("status") in _TERMINAL_STATUSES:
            continue
        label = _tool_or_item_label(str(it.get("text") or ""))
        if label in WORK_TOOL_NAMES:
            return f"Next is {label} — do not loop update_plan_status."
        return "Next is the pending work tool — do not loop update_plan_status."
    return "Next is score_gcr — do not loop update_plan_status."


def _infer_completed_from_untagged(
    previous: list[dict[str, str]],
    incoming: list[dict[str, str]],
) -> list[dict[str, str]]:
    keys = {_task_key(it["text"]) for it in incoming}
    incoming_tools = {
        _tool_or_item_label(it["text"])
        for it in incoming
        if _tool_or_item_label(it["text"]) in WORK_TOOL_NAMES
    }
    ran = _work_tools_ran_ok()
    out: list[dict[str, str]] = []
    inferred = False
    for it in previous:
        text = it.get("text") or ""
        status = it.get("status") or "pending"
        label = _tool_or_item_label(text)
        hit = _task_key(text) in keys or label in incoming_tools
        if (
            hit
            and status not in _TERMINAL_STATUSES
            and label in ran
        ):
            status = "completed"
            inferred = True
        out.append({"text": text, "status": status})
    if inferred:
        for i, it in enumerate(out):
            if it["status"] not in _TERMINAL_STATUSES:
                out[i] = {"text": it["text"], "status": "in_progress"}
                break
    return out


def _tool_or_item_label(text: str) -> str:
    blob = _norm_task_text(text)
    lower = blob.lower()
    for name in sorted(WORK_TOOL_NAMES, key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9_]){re.escape(name)}(?![a-z0-9_])", lower):
            return name
    if _INCOME_ITEM_RE.search(lower):
        return "compute_income"
    if _is_headline_count_intent(text) or _QUERY_UNITS_PHRASE_RE.search(lower):
        return "query_units"
    return blob


def _chosen_work_tool(tool_names: list[str] | None) -> str | None:
    for name in tool_names or []:
        n = str(name or "")
        if n in WORK_TOOL_NAMES:
            return n
    return None


def _item_maps_to_tool(item: dict[str, str], tool: str) -> bool:
    return _tool_or_item_label(str(item.get("text") or "")) == tool


def match_react_checklist_item(
    tool_names: list[str] | None,
    items: list[dict[str, str]] | None = None,
) -> tuple[int, dict[str, str]] | None:
    """Map a work-tool ReAct call to an existing checklist row. Never invents items."""
    rows = list(items) if items is not None else turn_ctx.checklist()
    tool = _chosen_work_tool(tool_names)
    if not rows or not tool:
        return None
    for i, it in enumerate(rows, 1):
        if it.get("status") == "in_progress" and _item_maps_to_tool(it, tool):
            return i, it
    for i, it in enumerate(rows, 1):
        if it.get("status") == "pending" and _item_maps_to_tool(it, tool):
            return i, it
    return None


def react_turn_task_suffix(
    tool_names: list[str] | None,
    items: list[dict[str, str]] | None = None,
) -> str:
    """Operator suffix: which numbered checklist task this ReAct tool is for."""
    rows = list(items) if items is not None else turn_ctx.checklist()
    tool = _chosen_work_tool(tool_names)
    if not rows or not tool:
        return ""
    hit = match_react_checklist_item(tool_names, rows)
    if hit is None:
        return "for (no matching task)"
    i, it = hit
    return f"for {i}. [{it.get('status') or 'pending'}] {it.get('text') or ''}"


def _query_units_unfiltered(occupancy: Any, vacant_only: Any) -> bool:
    if bool(vacant_only):
        return False
    occ = str(occupancy or "").strip().lower()
    return occ in {"", "all"}


def _is_headline_count_intent(text: str) -> bool:
    """True when a checklist line is a unit/occupancy/vacant/mtm *count*, not a list."""
    blob = _norm_task_text(text)
    lower = blob.lower()
    if not lower or _NON_HEADLINE_TOOLS_RE.search(lower):
        return False
    if _LIST_UNITS_RE.search(lower):
        return False
    if re.search(r"\bquery_units\b", lower):
        return True
    return bool(_HEADLINE_COUNT_RE.search(lower))


def plan_log_message(items: list[dict[str, str]], diff: dict[str, Any], *, tool: str) -> str:
    body = format_checklist(items) or "(empty checklist)"
    if diff.get("initial"):
        return f"{tool} — initial checklist\n{body}"
    if not diff.get("changed"):
        return f"{tool} — Plan unchanged\n{body}"
    bits: list[str] = []
    if diff.get("completed"):
        bits.append("completed: " + "; ".join(diff["completed"]))
    if diff.get("incomplete"):
        bits.append("incomplete: " + "; ".join(diff["incomplete"]))
    if diff.get("new"):
        bits.append("new: " + "; ".join(diff["new"]))
    was = list(diff.get("in_progress_was") or [])
    now = list(diff.get("in_progress") or [])
    if was != now:
        bits.append(f"in_progress: {', '.join(now) or '(none)'} (was {', '.join(was) or '(none)'})")
    delta = "; ".join(bits) if bits else "status/order change"
    return f"{tool} — checklist updated ({delta})\n{body}"


def create_plan(tasks: str) -> str:
    """Create this turn's ReAct checklist once. Intelligence is in the args.

    WHEN TO USE: FIRST action on every in-scope CRE ask (abstract / geo / underwrite
    / web_search news). Call ONCE even if there is only one work tool (a one-item
    checklist). Locate/map is one osm_geocode item — not query_units, compute_income,
    or compute_noi. Tag the current step [in_progress], the rest [pending].

    WHEN NOT: Do not skip because the ask is a single web_search or vault call.
    Do not call again — mark statuses with update_plan_status; rewrite the list
    with update_plan (never a second create_plan).
    Do not invent extra work. Match tools to the question — not always
    compute_income. Vacant-only can be a one-item checklist (query_units only).
    Different work tools MUST be separate numbered lines (1. osm_geocode
    2. query_units). Never one comma-joined item for two tools.
    One unfiltered query_units returns unit_count, occupied_count, vacant_count,
    occupancy_pct, and mtm_count (units on month-to-month leases). If several
    checklist intents are headline counts (units, occupancy, vacant, mtm_count),
    that is ONE unfiltered query_units item, not four. After an unfiltered
    query_units has run this turn, do not call query_units again for those counts.
    Extra query_units only to list a filtered subset of unit rows, not
    to re-fetch counts. compute_income still for in-place monthly $. Use
    counts for existence/yes-no rather than extra checklist items.

    What: `tasks` is a numbered checklist (string, JSON list, or items with status).
    How: Stores the list and emits the initial plan. A second call is rejected.
    """
    previous = turn_ctx.checklist()
    if previous:
        plan = format_checklist(previous)
        payload = {
            "plan": plan,
            "items": previous,
            "diff": checklist_diff(previous, previous),
            "changed": False,
            "log": "",
            "ok": False,
            "rejected": True,
            "hint": (
                "Checklist already exists. Use update_plan_status for statuses, "
                "or update_plan to replace the list — do not call create_plan again."
            ),
        }
        turn_ctx.record("create_plan", {"tasks": tasks}, payload)
        return (
            "Checklist already exists. Use update_plan_status for statuses, "
            "or update_plan to replace the list — do not call create_plan again.\n"
            f"{plan}"
        )
    items = parse_task_items(tasks)
    diff = checklist_diff([], items)
    plan = format_checklist(items)
    turn_ctx.set_checklist(items)
    turn_ctx.set_llm_plan(plan)
    log = plan_log_message(items, diff, tool="create_plan")
    payload = {
        "plan": plan,
        "items": items,
        "diff": diff,
        "changed": True,
        "log": log,
        "ok": True,
    }
    turn_ctx.record("create_plan", {"tasks": tasks}, payload)
    if not plan:
        return "Plan stored (empty checklist)."
    return f"Plan stored (initial checklist):\n{plan}"


def update_plan(tasks: str) -> str:
    """Replace this turn's checklist with a new plan. Status-only: update_plan_status.

    WHEN TO USE: need_replan (score_gcr G/C <= 2, failed search, wrong metro/query,
    steps cannot answer). Send the NEW remaining work only. Tag the current step
    [in_progress], the rest [pending]. Do not call create_plan again.

    WHEN NOT: After a successful vault/search/geocode/memo hit just to mark
    [completed] — that is update_plan_status. Do not use this as the first tool
    (create_plan first). Weather / small talk: no plan tools.

    What: `tasks` is a numbered checklist of new work (same shape as create_plan).
    How: Keeps existing [completed] and [incomplete] rows, then appends the new
    items. Does not drop done work. Duplicate text of a kept row is skipped.
    """
    previous = turn_ctx.checklist()
    if not previous:
        payload = {
            "plan": "",
            "items": [],
            "diff": checklist_diff([], []),
            "changed": False,
            "log": "",
            "ok": False,
            "rejected": True,
            "hint": "No checklist yet. Call create_plan first with the full numbered list.",
        }
        turn_ctx.record("update_plan", {"tasks": tasks}, payload)
        return "No checklist yet. Call create_plan first with the full numbered list."
    incoming = parse_task_items(tasks)
    if web_search_failed() and incoming and all(_is_search_task(it["text"]) for it in incoming):
        items = _mark_search_incomplete(previous)
        diff = checklist_diff(previous, items)
        plan = format_checklist(items)
        turn_ctx.set_checklist(items)
        turn_ctx.set_llm_plan(plan)
        log = plan_log_message(items, diff, tool="update_plan")
        payload = {
            "plan": plan,
            "items": items,
            "diff": diff,
            "changed": bool(diff["changed"]),
            "log": log,
            "ok": True,
            "search_down": True,
        }
        turn_ctx.record("update_plan", {"tasks": tasks}, payload)
        return (
            "Search already failed. Marked search [incomplete]. Do not call web_search again. "
            "Write your own user-facing sentence that metro news is unavailable, then "
            "score_gcr that sentence. Do not copy this tool result to the user."
        )
    if not incoming:
        plan = format_checklist(previous)
        payload = {
            "plan": plan,
            "items": previous,
            "diff": checklist_diff(previous, previous),
            "changed": False,
            "log": "",
            "ok": False,
            "rejected": True,
            "hint": "update_plan needs a numbered list of new tasks.",
        }
        turn_ctx.record("update_plan", {"tasks": tasks}, payload)
        return f"Kept original checklist (empty replacement).\n{plan}"
    items = merge_replan_checklist(previous, incoming)
    diff = checklist_diff(previous, items)
    plan = format_checklist(items)
    turn_ctx.set_checklist(items)
    turn_ctx.set_llm_plan(plan)
    log = plan_log_message(items, diff, tool="update_plan")
    payload = {
        "plan": plan,
        "items": items,
        "diff": diff,
        "changed": bool(diff["changed"]),
        "log": log,
        "ok": True,
        "replaced": True,
        "kept_completed": [
            it["text"] for it in items if it["status"] in _TERMINAL_STATUSES
        ],
    }
    turn_ctx.record("update_plan", {"tasks": tasks}, payload)
    return f"Plan replaced (kept completed/incomplete):\n{log}"


def update_plan_status(tasks: str) -> str:
    """Update statuses on the existing ReAct checklist. Does not replace the list.

    WHEN TO USE: Immediately after a real vault/search/geocode/memo tool (or after
    a reflection that changed statuses). Send the SAME items with that step
    [completed] (or [incomplete] if the tool could not answer) and the next
    [in_progress]. Status-only is the main job.

    WHEN NOT: Do not call update_plan_status twice in a row unless the next pending
    work tool already ran this run — then you may mark it [completed] or
    [incomplete]. After this tool returns, if any item is [pending] or
    [in_progress] and that tool has not run yet, the NEXT action MUST be that
    work tool — not update_plan_status. If every vault/work tool on the list
    already ran, or remaining items are [completed] or [incomplete], Next:
    score_gcr then answer. If a tool cannot supply the asked result, mark that
    item [incomplete] and do not retry it. After an unfiltered query_units
    this turn, leftover headline-count items (units, occupancy, vacant,
    mtm_count) are already filled — Next is compute_income (for monthly $) or
    score_gcr, not another query_units. Do not replace the list with a new
    one-liner. Do not invent a wholly new checklist here — that is update_plan.
    If items do not match the original, original items are kept and only matching
    status changes apply.

    What: `tasks` is the same numbered checklist with updated [pending] /
    [in_progress] / [completed] / [incomplete] tags (also [x], done,
    **in_progress**, incomplet). [incomplete] is terminal like [completed]
    but without an answer — missing data, empty/error tool result, or a
    field the tools cannot provide. Do not retry that item.
    How: Matches by item text and applies status. Extra unmatched lines are ignored.
    """
    previous = turn_ctx.checklist()
    if not previous:
        payload = {
            "plan": "",
            "items": [],
            "diff": checklist_diff([], []),
            "changed": False,
            "log": "",
            "ok": False,
            "rejected": True,
            "hint": "No checklist yet. Call create_plan first with the full numbered list.",
        }
        turn_ctx.record("update_plan_status", {"tasks": tasks}, payload)
        return "No checklist yet. Call create_plan first with the full numbered list."
    if _consecutive_status_without_work():
        plan = format_checklist(previous)
        hint = (
            "Rejected consecutive update_plan_status with no intervening work tool. "
            + _next_status_hint(previous)
        )
        log = f"update_plan_status — Plan unchanged\n{hint}\n{plan}"
        payload = {
            "plan": plan,
            "items": previous,
            "diff": checklist_diff(previous, previous),
            "changed": False,
            "log": log,
            "ok": False,
            "rejected": True,
            "hint": hint,
        }
        turn_ctx.record("update_plan_status", {"tasks": tasks}, payload)
        return f"Plan unchanged. {hint}\n{plan}"
    previous = _explode_task_items(previous)
    incoming = parse_task_items(tasks)
    if incoming and _tasks_omit_status_tags(tasks):
        items = _infer_completed_from_untagged(previous, incoming)
        matched = sum(
            1 for it in previous if _task_key(it["text"]) in {_task_key(x["text"]) for x in incoming}
        )
    else:
        items, matched = apply_status_updates(previous, incoming)
    diff = checklist_diff(previous, items)
    if not matched and incoming:
        diff = {**diff, "replaced_ignored": True}
    plan = format_checklist(items)
    turn_ctx.set_checklist(items)
    turn_ctx.set_llm_plan(plan)
    log = plan_log_message(items, diff, tool="update_plan_status")
    payload = {
        "plan": plan,
        "items": items,
        "diff": diff,
        "changed": bool(diff["changed"]),
        "log": log,
        "ok": True,
        "matched": matched,
    }
    turn_ctx.record("update_plan_status", {"tasks": tasks}, payload)
    if not matched and incoming:
        return (
            "Kept original checklist (incoming items did not match). "
            "Use update_plan_status with the same items and new statuses — "
            "do not replace the list here. Call update_plan to write a new plan.\n"
            f"{plan}"
        )
    if diff["changed"]:
        return f"Plan updated:\n{log}"
    return log


def reflection(analysis: str, need_replan: bool = False) -> str:
    """Store the model's analysis. Intelligence is in the args, not this body.

    WHEN TO USE:
    1. Once as a progress / SELF-CHECK after the vault tool the question needed
       (query_units, compute_income, compute_noi, or compute_cap_rate) — not after
       every tool.
    2. Error analysis when a tool failed or returned empty. If web_search is down,
       replan: skip market compare and say news is unavailable. If osm_geocode
       failed, replan: state uncertainty; optional search if geo_confidence is low.
       Do not loop the same failed call.
    3. Result synthesis before assembling the user-facing draft.
    4. SELF-CHECK after the vault tool the question needed. If incomplete, set
       need_replan, call update_plan with a NEW numbered list, then pick
       query_units vs compute_income — once, not until turn 12. Vacant-only does not require compute_income.
       Occupancy/income → compute_income.
       Do not always call compute_income. After a vault tool, call
       update_plan_status with that step [completed] and the next
       [in_progress], then continue. When the checklist is done, draft the
       user-facing sentence and call score_gcr. Do not require RollAbstract JSON.

    WHEN NOT: Do not call this after every tool; that burns the turn limit.
    Completeness lives in this tool, inside the same agent() loop. Python does not
    emit a leftover reflect, start a second run_agent, or inject compute_income.

    What: `analysis` is your reasoning. `need_replan` if the checklist is wrong.
    How: Stores analysis in turn context. If need_replan is true, this function does
    not rewrite the inventory — call update_plan with the new numbered list.
    """
    text = str(analysis or "").strip()
    turn_ctx.note_llm_kind("reflection")
    payload = {"analysis": text, "need_replan": bool(need_replan), "ok": True}
    turn_ctx.record("reflection", {"analysis": text, "need_replan": bool(need_replan)}, payload)
    if need_replan:
        if web_search_failed():
            return (
                "Search already failed. Do not call web_search again. "
                "Mark the search item [incomplete], then write your own sentence that "
                "metro news is unavailable and score_gcr that sentence. "
                "Do not copy this tool result to the user."
            )
        return (
            "Need replan: call update_plan with the NEW remaining work. "
            "Completed/incomplete rows stay on the list. Do not call create_plan again. "
            "Status-only changes use update_plan_status."
        )
    return f"Reflection stored: {text}" if text else "Reflection stored."


def ingest_noop_doc() -> str:
    return "ingest is executed by the API on attach, not as a model tool"


def query_units(occupancy: str | None = None, vacant_only: bool = False) -> str:
    """Read the session vault unit grid.

    When: rent-roll abstract, occupancy / vacant counts, unit mix. After ingest.
    What: occupancy filters unit occupancy status only (Occupied, Vacant, or
    all; omit or all = unfiltered). vacant_only lists vacancies.
    How: returns JSON summaries — never tenant names (PII already stripped).
    One unfiltered query_units returns unit_count, occupied_count, vacant_count,
    occupancy_pct, and mtm_count plus a note that those headline counts are
    complete in this result. Extra query_units only to list a filtered subset
    of unit rows, not to re-fetch counts. After an unfiltered query_units has
    run this turn, do not call query_units again for those counts.
    Do not call twice (occupied then vacant) for those counts.
    When not: follow-ups that only need a remembered number (use injected LTM
    FACT#); do not dump the full grid into a location or weather answer.
    """
    try:
        roll = _roll()
    except RuntimeError as exc:
        if _is_empty_vault_error(exc):
            return _need_file_json(
                "query_units", {"occupancy": occupancy, "vacant_only": vacant_only}
            )
        raise
    units = roll.units
    as_of = turn_ctx.asked_as_of() or roll.as_of
    if vacant_only:
        units = [u for u in units if u.vacant_on(as_of)]
    if occupancy and occupancy.strip().lower() not in {"all", ""}:
        units = [u for u in units if (u.occupancy or "").lower() == occupancy.lower()]
    payload = {
        "property_name": roll.property_name,
        "as_of": as_of,
        "geo_confidence": roll.geo_confidence,
        "suggested_address": roll.suggested_address,
        "unit_count": roll.unit_count,
        "occupied_count": roll.occupied_count_on(as_of),
        "vacant_count": roll.vacant_count_on(as_of),
        "occupancy_pct": round(roll.occupancy_pct_on(as_of), 4) if roll.unit_count else 0.0,
        "mtm_count": roll.mtm_count,
        "units": [u.model_dump() for u in units[:400]],
    }
    if _query_units_unfiltered(occupancy, vacant_only):
        payload["note"] = HEADLINE_COUNTS_NOTE
    turn_ctx.record("query_units", {"occupancy": occupancy, "vacant_only": vacant_only}, payload)
    return json.dumps(payload)


def _vacant_asking_rent(unit: Any) -> float:
    """Asking/market rent still on a vacant row, else leftover in_place_rent."""
    rent = float(getattr(unit, "in_place_rent", 0) or 0)
    if rent > 0:
        return rent
    extras = getattr(unit, "charge_codes", None) or {}
    if not isinstance(extras, dict):
        return 0.0
    for key, val in extras.items():
        lk = str(key).lower()
        if "market" not in lk and lk not in {"asking", "ask", "_market_rent"}:
            continue
        try:
            n = float(val or 0)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return 0.0


def compute_income(vacant_fill_rent: float | None = None) -> str:
    """Sum in-place monthly rent from the vaulted unit grid.

    When: in-place monthly, annualized income, or a 100% occupancy / all-units-
    occupied / no-vacant income estimate from the vaulted roll.
    What: optional vacant_fill_rent — monthly $ the user stated to fill each vacant
    unit (scenario). Omit for vault/asking-rent full occupancy.
    How: in_place_monthly is occupied units only; vacant in-place is zero.
    Return JSON with monthly, annualized, occupied_count, vacant_count,
    occupancy_pct, mtm_count (units on month-to-month leases),
    mean_occupied_rent, vacant_asking_monthly, and
    estimated_full_occupancy_monthly. If vacant_fill_rent is set, also return
    vacant_fill_monthly, vacant_fill_annual, scenario_full_occupancy_monthly,
    and scenario_full_occupancy_annual (in_place + vacant_count × fill).
    For hypothetical full occupancy, cite estimated_full_occupancy_monthly —
    do not repeat in_place_monthly. With a user fill rent, cite
    scenario_full_occupancy_annual / monthly — not vacant fill alone.
    Use counts for existence/yes-no.
    When not: do not invent rent; do not geocode or web-search.
    """
    try:
        roll = _roll()
    except RuntimeError as exc:
        if _is_empty_vault_error(exc):
            return _need_file_json(
                "compute_income", {"vacant_fill_rent": vacant_fill_rent}
            )
        raise
    as_of = turn_ctx.asked_as_of() or roll.as_of
    monthly = roll.in_place_monthly_on(as_of)
    occupied = roll.occupied_count_on(as_of)
    vacant = roll.vacant_count_on(as_of)
    occ_pct = round(occupied / roll.unit_count, 4) if roll.unit_count else 0.0
    mean_occ = round(monthly / occupied, 2) if occupied else None
    vacant_asking = round(
        sum(_vacant_asking_rent(u) for u in roll.units if u.vacant_on(as_of)),
        2,
    )
    if vacant_asking > 0:
        full_occ = round(monthly + vacant_asking, 2)
        full_occ_method = "in_place_monthly + vacant_asking_monthly"
    elif occupied and roll.unit_count:
        full_occ = round((monthly / occupied) * roll.unit_count, 2)
        full_occ_method = "mean_occupied_rent * unit_count"
    else:
        full_occ = None
        full_occ_method = None
    payload = {
        "in_place_monthly": round(monthly, 2),
        "in_place_annual": round(monthly * 12, 2),
        "occupied_count": occupied,
        "vacant_count": vacant,
        "unit_count": roll.unit_count,
        "occupancy_pct": occ_pct,
        "mean_occupied_rent": mean_occ,
        "vacant_asking_monthly": vacant_asking,
        "estimated_full_occupancy_monthly": full_occ,
        "estimated_full_occupancy_method": full_occ_method,
        "note": (
            "in_place_monthly is occupied units only. For 100% occupancy / all "
            "units occupied / no-vacant income, use estimated_full_occupancy_monthly "
            "(not in_place_monthly)."
        ),
        "mtm_count": roll.mtm_count,
        "as_of": as_of,
        "property_name": roll.property_name,
    }
    if vacant_fill_rent is not None:
        fill = float(vacant_fill_rent)
        fill_m = round(vacant * fill, 2)
        payload["vacant_fill_rent"] = round(fill, 2)
        payload["vacant_fill_monthly"] = fill_m
        payload["vacant_fill_annual"] = round(fill_m * 12, 2)
        payload["scenario_full_occupancy_monthly"] = round(monthly + fill_m, 2)
        payload["scenario_full_occupancy_annual"] = round((monthly + fill_m) * 12, 2)
        payload["note"] = (
            payload["note"]
            + " User-stated vacant fill: cite scenario_full_occupancy_annual "
            "(in_place_annual + vacant_fill_annual), not vacant_fill_annual alone."
        )
    args = {}
    if vacant_fill_rent is not None:
        args["vacant_fill_rent"] = vacant_fill_rent
    turn_ctx.record("compute_income", args, payload)
    return json.dumps(payload)


def compute_noi(operating_expenses: float | None = None) -> str:
    """NOI = annualized in-place income minus stated operating expenses.

    When: underwriting / Q3 when the user states expenses.
    What: operating_expenses annual dollars, or omit.
    How: if expenses missing, return noi_incomplete=true — do not guess opex.
    When not: Q1 abstract; location-only questions.
    """
    try:
        roll = _roll()
    except RuntimeError as exc:
        if _is_empty_vault_error(exc):
            return _need_file_json("compute_noi", {"operating_expenses": operating_expenses})
        raise
    income = round(roll.in_place_monthly * 12, 2)
    incomplete = operating_expenses is None
    noi = None if incomplete else round(income - float(operating_expenses), 2)
    payload = {
        "in_place_annual": income,
        "operating_expenses": operating_expenses,
        "noi": noi,
        "noi_incomplete": incomplete,
    }
    turn_ctx.record("compute_noi", {"operating_expenses": operating_expenses}, payload)
    return json.dumps(payload)


def compute_cap_rate(ask_price: float, noi: float | None = None) -> str:
    """Implied cap = NOI / ask. Uses annualized income if NOI was not computed.

    When: underwriting / Q3 only if the user gives an ask price.
    What: ask_price required; noi optional (else income is not a true NOI cap).
    How: return JSON; if using income not NOI, flag implied_on_income=true.
    When not: Q1, Q2, or when no ask was provided — skip implied cap.
    """
    try:
        roll = _roll()
    except RuntimeError as exc:
        if _is_empty_vault_error(exc):
            return _need_file_json("compute_cap_rate", {"ask_price": ask_price, "noi": noi})
        raise
    if ask_price <= 0:
        raise ValueError("ask_price must be positive")
    income = roll.in_place_monthly * 12
    basis = float(noi) if noi is not None else income
    payload = {
        "ask_price": ask_price,
        "basis": round(basis, 2),
        "implied_cap": round(basis / ask_price, 6),
        "implied_on_income": noi is None,
        "note": "NOI incomplete — cap uses in-place income" if noi is None else "cap uses NOI",
    }
    turn_ctx.record("compute_cap_rate", {"ask_price": ask_price, "noi": noi}, payload)
    return json.dumps(payload)


def osm_geocode(query: str) -> str:
    """Geocode a property via OpenStreetMap Nominatim (MCP OSM equivalent).

    When: Locator / Q2. Always call even if ingest left an address hint.
    What: query string — hint address or property name + city.
    How: public Nominatim; returns display_name, lat, lon. On error, say so (harness replans).
    When not: RollAbstractor / Q1; Underwriter / Q3 (OSM is forbidden there).
    Query must be a place or street+city — never a CSV/xlsx filename.
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("geocode query is empty")
    headers = {"User-Agent": "cre-deal-rentroll/0.1 (interview harness)"}
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(NOMINATIM, params={"q": q, "format": "json", "limit": 1}, headers=headers)
            r.raise_for_status()
            hits = r.json()
    except Exception as exc:  # harness reflection #2
        payload = {"ok": False, "error": str(exc)}
        turn_ctx.record("osm_geocode", {"query": q}, payload)
        return json.dumps(payload)
    if not hits:
        payload = {"ok": False, "error": "no OSM hits", "query": q}
        turn_ctx.record("osm_geocode", {"query": q}, payload)
        return json.dumps(payload)
    hit = hits[0]
    payload = {
        "ok": True,
        "display_name": hit.get("display_name"),
        "lat": float(hit["lat"]),
        "lon": float(hit["lon"]),
        "osm_id": hit.get("osm_id"),
    }
    turn_ctx.record("osm_geocode", {"query": q}, payload)
    return json.dumps(payload)


def web_search(query: str, max_results: int = 5) -> str:
    """Search the public web (ddgs) for current articles.

    When: Q2 only if geo_confidence is low; Q3 multifamily cap-rate / sale news
    for THIS property's city/metro.
    What: query string; max_results default 5.
    How: returns title/href/snippet JSON. On failure return error JSON so the
    harness can reflect and replan (do not loop).
    When not: Q1 rent-roll abstract — forbidden. Do not search for tenant names.
    """
    if turn_ctx.search_should_fail() or os.environ.get("CRE_FORCE_SEARCH_FAIL") == "1":
        payload = {"ok": False, "error": "web_search forced failure (search-down fixture)"}
        turn_ctx.record("web_search", {"query": query}, payload)
        raise RuntimeError(payload["error"])
    try:
        from ddgs import DDGS

        rows = list(DDGS().text(query, max_results=max_results))
        payload = {
            "ok": True,
            "results": [
                {"title": r.get("title"), "href": r.get("href"), "body": r.get("body")} for r in rows
            ],
        }
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        turn_ctx.record("web_search", {"query": query}, payload)
        raise RuntimeError(str(exc)) from exc
    turn_ctx.record("web_search", {"query": query}, payload)
    return json.dumps(payload)


def publish_memo(title: str, body: str) -> str:
    """Write an underwriting memo to the deal file (sensitive).

    When: only when the user asks to publish / save a memo (Q-hitl).
    What: title and body text. Not required for Q1–Q3 answers.
    How: HITL must approve in the UI before this runs. Session is saved first.
    When not: answering abstracts, maps, or cap-rate questions.
    """
    if not turn_ctx.hitl_allowed():
        pending = {"tool": "publish_memo", "title": title, "body": body}
        turn_ctx.record("publish_memo", pending, {"status": "pending_hitl"})
        return json.dumps({"status": "pending_hitl", "title": title})
    payload = {"status": "published", "title": title, "bytes": len(body or "")}
    turn_ctx.record("publish_memo", {"title": title}, payload)
    return json.dumps(payload)


def score_gcr(draft: str) -> str:
    """Score the sentence you would show the user (G / request-C / R + vault_calc).

    WHEN TO USE: SELF-CHECK before the user-facing answer. Required before text-only
    end_turn on a CRE/rent-roll answer. Call once with the exact sentence you would
    show. Forbidden to stop with a sentence and no score_gcr. If min(G, request_C) <= 2,
    need_replan / call more vault tools, then score_gcr again. vault_calc N/A (a cited
    number not in the vault allowlist, including derived math) is not a fail. Do not
    return to the user until G/C are ok or you hit the turn limit. Do not require
    RollAbstract JSON.

    WHEN NOT: Not after every MCP hit. Not a substitute for query_units / compute_*.
    Intelligence is in choosing WHEN; this body runs the live judge + vault_calc_ok.
    """
    from cre_rentroll.eval_service import live_judge, vault_calc_invented_hits, vault_calc_log_label

    text = str(draft or "").strip()
    query = turn_ctx.eval_query() or ""
    roll = None
    try:
        roll = _roll()
    except Exception:
        user = turn_ctx.username()
        if user:
            roll = vault.load_roll(user)
    traces = list(turn_ctx.traces())
    before = turn_ctx.llm_call_kinds().count("judge")
    scores = live_judge(query, text, roll, traces)
    if scores.judge == "llm":
        turn_ctx.add_llm_if_unchanged("judge", before)
    g = scores.G or 0
    c = scores.request_C or 0
    hits = (
        vault_calc_invented_hits(text, roll, traces) if scores.vault_calc_ok is False else []
    )
    invented = [h["token"] for h in hits]
    vault_label = vault_calc_log_label(
        scores.vault_calc_ok, hits, draft=text, roll=roll
    )
    scores.vault_calc = vault_label
    turn_ctx.set_eval_scores(scores)
    if scores.judge in {"error", "empty"}:
        decision = "need_replan"
        hint = (
            "need_replan — call update_plan with NEW remaining work (completed "
            "rows are kept), then those work tools, then score_gcr again"
        )
    elif scores.vault_calc_ok is False or min(g, c) <= 2:
        decision = "need_replan"
        hint = (
            "need_replan — call update_plan with NEW remaining work (not create_plan; "
            "completed rows are kept), then those work tools, then score_gcr again"
        )
        if invented:
            hint = f"{hint}; {vault_label}"
    else:
        decision = "ok"
        hint = "ok to show this sentence"
    payload = {
        "R": scores.R,
        "G": scores.G,
        "request_C": scores.request_C,
        "vault_calc_ok": scores.vault_calc_ok,
        "vault_invented": invented,
        "vault_calc": vault_label,
        "judge": scores.judge,
        "decision": decision,
        "hint": hint,
        "ok": True,
        "reason": scores.reason,
    }
    turn_ctx.record("score_gcr", {"draft": text}, payload)
    return json.dumps(payload)


TOOL_IMPLS: dict[str, Any] = {
    "create_plan": create_plan,
    "update_plan": update_plan,
    "update_plan_status": update_plan_status,
    "reflection": reflection,
    "score_gcr": score_gcr,
    "query_units": query_units,
    "compute_income": compute_income,
    "compute_noi": compute_noi,
    "compute_cap_rate": compute_cap_rate,
    "osm_geocode": osm_geocode,
    "web_search": web_search,
    "publish_memo": publish_memo,
}
