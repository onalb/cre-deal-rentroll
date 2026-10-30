from __future__ import annotations

import functools
import json
import os
import re
from typing import Any

from cre_rentroll import turn_ctx, vault
from cre_rentroll.schemas import CanonicalRentRoll

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
        "ask_prediction_agent",
        "osm_geocode",
        "web_search",
    }
)
_HEADLINE_COUNT_RE = re.compile(
    r"\b(unit_count|unit count|occupied_count|vacant_count|occupancy_pct|"
    r"occupancy|occupied|vacant|units|mtm_count|mtm|month[\s-]*to[\s-]*month|"
    r"avg_sqft|average\s+sqft|typical unit size|square footage|\bsqft\b)\b",
    re.I,
)
_LIST_UNITS_RE = re.compile(
    r"\b(list|enumerat\w*|unit rows|unit ids?|row-by-row|names of)\b",
    re.I,
)
_NON_HEADLINE_TOOLS_RE = re.compile(
    r"\b(compute_income|compute_noi|compute_cap_rate|ask_vacancy|ask_prediction_agent|osm_geocode|web_search|"
    r"score_gcr|reflection)\b",
    re.I,
)
# Checklist prose → vault tool (exact snake_case names are matched first).
_INCOME_ITEM_RE = re.compile(
    r"\b(compute[\s_-]*income|in[\s_-]*place|monthly)\b",
    re.I,
)
_QUERY_UNITS_PHRASE_RE = re.compile(r"\bquery[\s_-]*units\b", re.I)
HEADLINE_COUNTS_NOTE = (
    "These headline counts are complete in this result (including avg_sqft); "
    "another query_units is only needed to LIST a filtered subset of unit rows, "
    "not to re-fetch counts."
)


def _roll() -> CanonicalRentRoll:
    user = turn_ctx.username() or os.environ.get("CRE_RENTROLL_USER")
    if not user:
        raise RuntimeError("no username in turn context")
    roll = vault.load_roll(user)
    if roll is None:
        raise RuntimeError("no rent roll in the session vault — attach a CSV/xlsx first")
    return roll


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


_TERMINAL_STATUSES = frozenset({"completed", "incomplete"})


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


def _last_work_observation() -> tuple[str, str]:
    """Last real work tool this turn, and ok or error. Empty when none has run."""
    tool = ""
    status = ""
    for row in turn_ctx.traces() or []:
        name = str(row.get("name") or "")
        if name not in WORK_TOOL_NAMES:
            continue
        tool = name
        result = row.get("result")
        if isinstance(result, dict) and result.get("ok") is False:
            status = "error"
        else:
            status = "ok"
    return tool, status


def _deal_consecutive_hint(body: dict[str, Any]) -> str:
    if body.get("all_terminal"):
        nxt = "Next is score_gcr — do not loop update_plan_status."
    elif body.get("next_work"):
        nxt = f"Next is {body['next_work']} — do not loop update_plan_status."
    else:
        nxt = "Next is the pending work tool — do not loop update_plan_status."
    return "Rejected consecutive update_plan_status with no intervening work tool. " + nxt


def _deal_reflection_text(body: dict[str, Any]) -> str:
    text = str(body.get("analysis") or "").strip()
    if body.get("need_replan"):
        from cre_rentroll.eval_service import (
            ask_is_conceptual,
            ask_is_market_area,
            need_replan_hint,
        )

        query = turn_ctx.eval_query() or ""
        if ask_is_conceptual(query) or ask_is_market_area(query):
            return need_replan_hint(query)
        return (
            "Need replan: call update_plan with the NEW remaining work. "
            "Completed/incomplete rows stay on the list. Do not call create_plan again. "
            "Status-only changes use update_plan_status."
        )
    return f"Reflection stored: {text}" if text else "Reflection stored."


def _deal_model_text(name: str, body: dict[str, Any]) -> str:
    reason = str(body.get("reason") or "")
    if name == "reflection":
        return _deal_reflection_text(body)
    if reason == "consecutive_status":
        hint = _deal_consecutive_hint(body)
        plan = str(body.get("plan") or "")
        body["hint"] = hint
        body["log"] = f"update_plan_status — Plan unchanged\n{hint}\n{plan}"
        return f"Plan unchanged. {hint}\n{plan}"
    return str(body.get("text") or "")


def _dispatch_plan(name: str, model_args: dict[str, Any]) -> str:
    """POST the one MCP. The gateway returns the next checklist. No local fallback."""
    from cre_rentroll.capabilities_client import GATEWAY_REQUIRED, call_deal_tool
    from cre_rentroll.mcp_gateway import mcp_url

    recorded = dict(model_args)
    if name == "reflection":
        recorded["analysis"] = str(model_args.get("analysis") or "").strip()
        recorded["need_replan"] = bool(model_args.get("need_replan"))
    from cre_rentroll.capabilities_client import gateway_base

    if not mcp_url() and not gateway_base():
        err = {"ok": False, "error": GATEWAY_REQUIRED, "rejected": True}
        turn_ctx.record(name, recorded, err)
        return json.dumps(err)
    last_tool, last_status = _last_work_observation()
    remote = dict(recorded)
    remote["checklist"] = turn_ctx.checklist()
    remote["last_work_tool"] = last_tool
    remote["last_work_status"] = last_status
    remote["ran_ok"] = sorted(_work_tools_ran_ok())
    remote["status_since_work"] = _consecutive_status_without_work()
    try:
        body = json.loads(call_deal_tool(name, remote))
    except Exception as exc:
        err = {
            "ok": False,
            "error": str(exc),
            "rejected": True,
            "source": "capabilities-gateway",
        }
        turn_ctx.record(name, recorded, err)
        return json.dumps(err)
    if not isinstance(body, dict):
        err = {
            "ok": False,
            "error": "capabilities gateway returned a non-object",
            "rejected": True,
        }
        turn_ctx.record(name, recorded, err)
        return json.dumps(err)
    checklist = body.get("checklist")
    if isinstance(checklist, list):
        turn_ctx.set_checklist(checklist)
    if name != "reflection":
        turn_ctx.set_llm_plan(str(body.get("plan") or ""))
    shown = _deal_model_text(name, body)
    if name == "reflection":
        body["analysis"] = recorded["analysis"]
        body["need_replan"] = recorded["need_replan"]
        body["ok"] = True
    turn_ctx.record(name, recorded, body)
    return shown


def create_plan(tasks: str) -> str:
    """Create this turn's ReAct checklist once. Intelligence is in the args.

    WHEN TO USE: FIRST action on every in-scope CRE ask (abstract / geo / underwrite
    / web_search news / CRE definitions). Call ONCE. One numbered line per needed
    work tool — not per comma in the user sentence. A one-item checklist is fine
    when that one tool answers the whole last message (this-deal occupancy only →
    query_units; map-only → osm_geocode; vacant-only → query_units; definition of
    a rent roll → web_search; average property value in that area / metro / comps / area/market
    facts → web_search; zip/city in the message is enough; in that area uses last
    geocode/address_hint; one web_search). Only if THIS last message asks for
    map AND month-to-month AND cap: list osm_geocode, query_units, and
    compute_cap_rate as three items. Never copy that three-item list onto a
    definition or occupancy-only ask.     Locate/map with no other CRE ask is one
    osm_geocode item — not query_units, compute_income, or compute_noi. If they
    correct the last pin's country/place or say search the map again, last User
    message wins: web_search then osm_geocode from FACT# property_name,
    unit_count, as_of, address_hint constrained by what they said — not the
    previous OSM town. When the roll is already ingested, never the need-file
    sentence for that follow-up. Never
    osm_geocode / compute_cap_rate / compute_noi unless THIS last message asked.
    Tag the current step [in_progress], the rest [pending].

    WHEN NOT: Talking to the agent as a person or idle chat (identity, greetings,
    the agent's name, chitchat) — do not call this tool. Idle weekend weather is
    OOS; facts about the property's area or market stay in scope.
    Do not skip because the ask is a single web_search or vault call.
    Do not call again — mark statuses with update_plan_status; rewrite the list
    with update_plan (never a second create_plan).
    If you must call a work tool that is not on the checklist, update_plan with
    the missing items FIRST, then that tool.
    Do not invent extra work. Match tools to THIS last message — not always
    compute_income, and not prior-session Multi-2/Multi-3 tools sitting in memory.
    Vacant-only can be a one-item checklist (query_units only). Different work
    tools MUST be separate numbered lines. If THIS last message asks map AND MTM
    AND cap, that is 1. osm_geocode  2. query_units  3. compute_cap_rate — never
    one comma-joined item for two tools. That example is conditional, not a
    default checklist.
    One unfiltered query_units returns unit_count, occupied_count, vacant_count,
    occupancy_pct, mtm_count (units on month-to-month leases), and avg_sqft
    (mean of units with sqft>0). If several checklist intents are headline counts
    (units, occupancy, vacant, mtm_count, avg_sqft / typical unit size),
    that is ONE unfiltered query_units item, not four. After an unfiltered
    query_units has run this turn, do not call query_units again for those counts.
    Extra query_units only to list a filtered subset of unit rows, not
    to re-fetch counts. compute_income still for in-place monthly $. Use
    counts for existence/yes-no rather than extra checklist items.

    What: `tasks` is a numbered checklist (string, JSON list, or items with status).
    How: Stores the list and emits the initial plan.     A second call is rejected.
    """
    return _dispatch_plan("create_plan", {"tasks": tasks})


def update_plan(tasks: str) -> str:
    """Replace this turn's checklist with a new plan. Status-only: update_plan_status.

    WHEN TO USE: need_replan (score_gcr G/C <= 2, failed search, wrong metro/query,
    steps cannot answer), or a needed work tool is missing from the checklist.
    Send the NEW remaining work only (add omitted workstreams). Tag the current step
    [in_progress], the rest [pending]. Do not call create_plan again. Never run an
    off-list work tool before this call. If the last ask is a CRE definition and
    the draft was off-topic deal numbers, do not add vault calculators or
    osm_geocode — rewrite a cited definition (web_search if needed).

    WHEN NOT: After a successful vault/search/geocode hit just to mark
    [completed] — that is update_plan_status. Do not use this as the first tool
    in a normal turn (create_plan first) — but in a PLANLESS turn (no
    create_plan on the menu) this IS the escape hatch: call it with the new
    numbered list to seed the checklist, then the extra work tool.
    Weather / identity / greetings / chitchat: no plan tools.

    What: `tasks` is a numbered checklist of new work (same shape as create_plan).
    How: Keeps existing [completed] and [incomplete] rows, then appends the new
    items. Does not drop done work. Duplicate text of a kept row is skipped.
    """
    return _dispatch_plan("update_plan", {"tasks": tasks})


def update_plan_status(tasks: str) -> str:
    """Update statuses on the existing ReAct checklist. Does not replace the list.

    WHEN TO USE: Immediately after a real vault/search/geocode tool (or after
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
    return _dispatch_plan("update_plan_status", {"tasks": tasks})


def reflection(analysis: str, need_replan: bool = False) -> str:
    """Store the model's analysis. Intelligence is in the args, not this body.

    WHEN TO USE:
    1. Once as a progress / SELF-CHECK after the vault tool the question needed
       (query_units, compute_income, compute_noi, or compute_cap_rate) — not after
       every tool.
    2. Error analysis when a tool failed or returned empty. If web_search is down,
       replan: do not retry; mention news/comps only if the user asked. If osm_geocode
       failed, replan: state uncertainty; optional search if geo_confidence is low.
       Do not loop the same failed call.
    3. Result synthesis before assembling the user-facing draft.
    4. SELF-CHECK after the vault tool the question needed. If incomplete, set
       need_replan, call update_plan with a NEW numbered list, then pick
       query_units vs compute_income — once, not until turn 12. Vacant-only does not require compute_income.
       Occupancy/income (this deal) → compute_income. Definition asks: do not add
       vault calculators; rewrite a cited definition (web_search if needed).
       Area/metro/comps: do not add vault calculators and do not rewrite a rent-roll
       definition; cite listings / a stated average (web_search if needed).
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
    return _dispatch_plan(
        "reflection",
        {"analysis": str(analysis or ""), "need_replan": bool(need_replan)},
    )


def ingest_noop_doc() -> str:
    return "ingest is executed by the API on attach, not as a model tool"


def _capability(name: str, arguments: dict[str, Any]) -> str:
    """POST this tool to the capabilities gateway. No local math."""
    from cre_rentroll.capabilities_client import GATEWAY_REQUIRED, call_deal_tool
    from cre_rentroll.mcp_gateway import mcp_url

    payload_args = {key: value for key, value in arguments.items() if value is not None}
    user = turn_ctx.username() or os.environ.get("CRE_RENTROLL_USER") or ""
    from cre_rentroll.capabilities_client import gateway_base

    if not mcp_url() and not gateway_base():
        err = {"ok": False, "error": GATEWAY_REQUIRED}
        turn_ctx.record(name, payload_args, err)
        return json.dumps(err)
    remote_args = dict(payload_args)
    remote_args["username"] = user
    if mcp_url() and name in {
        "query_units",
        "compute_income",
        "compute_noi",
        "compute_cap_rate",
    }:
        try:
            from cre_rentroll.vault import load_roll

            roll = load_roll(user)
        except Exception:
            roll = None
        if roll is not None:
            remote_args["roll"] = roll.model_dump()
    if name in {
        "query_units",
        "compute_income",
        "compute_noi",
        "compute_cap_rate",
        "ask_prediction_agent",
    }:
        asked = turn_ctx.asked_as_of()
        if asked:
            remote_args["as_of"] = asked
    if name == "ask_prediction_agent" and not str(remote_args.get("question") or "").strip():
        remote_args["question"] = (turn_ctx.eval_query() or "").strip()
    try:
        text = call_deal_tool(name, remote_args)
    except Exception as exc:
        err = {"ok": False, "error": str(exc), "source": "capabilities-gateway"}
        turn_ctx.record(name, payload_args, err)
        if name == "web_search":
            raise RuntimeError(str(exc)) from exc
        return json.dumps(err)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"text": text, "ok": True}
    if not isinstance(result, dict):
        result = {"text": text, "ok": True}
    if name == "ask_prediction_agent" and "awaiting_followup" in result:
        from cre_rentroll.predict_followup import note_awaiting_followup

        note_awaiting_followup(user, bool(result.get("awaiting_followup")))
    turn_ctx.record(name, payload_args, result)
    if name == "web_search" and result.get("ok") is False:
        raise RuntimeError(str(result.get("error") or "web_search failed"))
    if name == "compute_cap_rate" and result.get("error"):
        raise ValueError(str(result["error"]))
    return text


def query_units(occupancy: str | None = None, vacant_only: bool = False) -> str:
    """Read the session vault unit grid through the capabilities gateway.

    When: rent-roll abstract, occupancy / vacant counts, unit mix, average or
    typical unit size (sqft). After ingest.
    What: occupancy filters unit occupancy status only (Occupied, Vacant, or
    all; omit or all = unfiltered). vacant_only lists vacancies.
    How: POST the one MCP tool query_units. Returns JSON
    summaries — never tenant names (PII already stripped).
    One unfiltered query_units returns unit_count, occupied_count, vacant_count,
    occupancy_pct, mtm_count, and avg_sqft (mean of units with sqft>0; null if
    none) plus a note that those headline counts are complete in this result.
    Average / typical unit size is avg_sqft in this JSON — do not hand-average
    the units grid, and do not say the rent roll does not specify square footage
    when avg_sqft is present. Extra query_units only to list a filtered subset
    of unit rows, not to re-fetch counts. After an unfiltered query_units has
    run this turn, do not call query_units again for those counts.
    Do not call twice (occupied then vacant) for those counts.
    When not: follow-ups that only need a remembered number (use injected LTM
    FACT#); do not dump the full grid into a location or weather answer.
    If CAPABILITIES_GATEWAY_URL is unset, returns an error that the capabilities
    gateway is required.
    """
    return _capability("query_units", {"occupancy": occupancy, "vacant_only": vacant_only})


def compute_income(vacant_fill_rent: float | None = None) -> str:
    """Sum in-place monthly rent from the vaulted unit grid via the capabilities gateway.

    When: in-place monthly, annualized income, or a 100% occupancy / all-units-
    occupied / no-vacant income estimate from the vaulted roll.
    What: optional vacant_fill_rent — monthly $ the user stated to fill each vacant
    unit (scenario). Omit for vault/asking-rent full occupancy.
    How: POST the one MCP tool compute_income.
    in_place_monthly is occupied units only; vacant in-place is zero.
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
    If CAPABILITIES_GATEWAY_URL is unset, returns an error that the capabilities
    gateway is required.
    """
    args: dict[str, Any] = {}
    if vacant_fill_rent is not None:
        args["vacant_fill_rent"] = vacant_fill_rent
    return _capability("compute_income", args)


def compute_noi(operating_expenses: float | None = None) -> str:
    """NOI = annualized in-place income minus stated operating expenses.

    When: underwriting / Q3 when the user states expenses.
    What: operating_expenses annual dollars, or omit.
    How: POST the one MCP tool compute_noi.
    If expenses missing, the gateway returns noi_incomplete=true — do not guess opex.
    When not: Q1 abstract; location-only questions.
    If CAPABILITIES_GATEWAY_URL is unset, returns an error that the capabilities
    gateway is required.
    """
    return _capability("compute_noi", {"operating_expenses": operating_expenses})


def compute_cap_rate(ask_price: float, noi: float | None = None) -> str:
    """Implied cap = NOI / ask. Uses annualized income if NOI was not computed.

    When: underwriting / Q3 only if the user gives an ask price.
    What: ask_price required; noi optional (else income is not a true NOI cap).
    How: POST the one MCP tool compute_cap_rate.
    If using income not NOI, the gateway flags implied_on_income=true.
    When not: Q1, Q2, or when no ask was provided — skip implied cap.
    If CAPABILITIES_GATEWAY_URL is unset, returns an error that the capabilities
    gateway is required.
    """
    return _capability("compute_cap_rate", {"ask_price": ask_price, "noi": noi})


def osm_geocode(query: str) -> str:
    """Geocode a property via the capabilities gateway (OpenStreetMap Nominatim).

    When: Locator / Q2. Always call even if ingest left an address hint.
    What: query string — hint address or property name + city.
    How: POST the one MCP tool osm_geocode.
    Returns display_name, lat, lon. On error, say so (harness replans).
    When not: RollAbstractor / Q1; Underwriter / Q3 (OSM is forbidden there).
    Query must be a place or street+city — never a CSV/xlsx filename.
    If the last User message corrected the country/place, constrain the query
    with that correction and roll FACT# (property_name, unit_count, as_of,
    address_hint); do not reuse the previous OSM town. Never invent a street.
    If CAPABILITIES_GATEWAY_URL is unset, returns an error that the capabilities
    gateway is required.
    """
    return _capability("osm_geocode", {"query": query})


def web_search(query: str, max_results: int = 5) -> str:
    """Search the public web through the capabilities gateway.

    When: average property value in that area / metro / comps; area/market
    facts (demographics, hazards, comps). 'In that area' uses last
    geocode/address_hint; zip/city in the message is enough. Q2 if
    geo_confidence is low or the user corrected the last pin's country/place
    (search using FACT# property_name, unit_count, as_of, address_hint plus
    that correction — not the previous OSM town); Q3 multifamily cap-rate /
    sale news for THIS property's city/metro (FACT# / address_hint / last
    geocode — never invent a metro). One-item create_plan is enough. Never
    require a spreadsheet for that ask. Python does not write a canned figure.
    What: query string; max_results default 5.
    How: POST the one MCP tool web_search.
    Returns title/href/snippet JSON. On failure the error is raised so the
    harness can reflect and replan (do not loop).
    When not: Q1 rent-roll abstract — forbidden. Do not search for tenant names.
    If CAPABILITIES_GATEWAY_URL is unset, returns an error that the capabilities
    gateway is required.
    """
    return _capability("web_search", {"query": query, "max_results": max_results})


def score_gcr(draft: str) -> str:
    """Score the sentence you would show the user (G / request-C / R + vault_calc).

    WHEN TO USE: SELF-CHECK before the user-facing answer. Required before text-only
    end_turn on a CRE/rent-roll answer. Call once with the exact sentence you would
    show. Forbidden to stop with a sentence and no score_gcr. If min(G, request_C) <= 2,
    need_replan, then score_gcr again. Do not add vault calculators when the ask is a
    CRE definition and the draft was off-topic deal numbers — rewrite a cited
    definition (web_search if needed). Area/market facts (demographics, hazards,
    comps) are in-scope CRE: do not rewrite a rent-roll definition or score as
    off-topic rent-roll; citations/search hits are the payload. Do not require
    occupancy numbers. Locate / map / last-pin country or place correction is
    on-topic locate: a cited osm_geocode/map sentence is the payload — do not
    score G=1 off-topic or hide it. Do not require occupancy numbers.
    If the last ask_prediction_agent result is present, quoting that vacancy
    reply is the complete payload for THIS turn (predicted rate or instructions
    to the user). Do not ding C for omitting occupancy_pct / in_place_monthly,
    and do not substitute query_units occupancy. Set instructional true when
    that reply asks the user to act before a score can be given.
    vault_calc N/A (a cited
    number not in the vault allowlist, including derived math) is not a fail. Conceptual
    CRE definitions and area/market drafts do not need occupancy/income/unit figures. Do not return to the user
    until G/C are ok or you hit the turn limit. Do not require RollAbstract JSON.

    WHEN NOT: Not after every MCP hit. Not a substitute for query_units / compute_*.
    Not for out-of-scope identity, greetings, weather, or chitchat (no tools at all).
    Intelligence is in choosing WHEN; this body runs the live judge + vault_calc_ok.
    """
    from cre_rentroll.eval_service import (
        need_replan_hint,
        prediction_clarify_draft,
        vault_calc_invented_hits,
        vault_calc_log_label,
    )
    from cre_rentroll.score_gcr_lambda import judge

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
    # Session stays here. The handler only runs live_judge.
    scores = judge(query, text, roll, traces)
    if scores.judge == "llm":
        turn_ctx.add_llm_if_unchanged("judge", before)
    clarify = prediction_clarify_draft(text, traces)
    if clarify and scores.judge == "llm":
        if isinstance(scores.request_C, int):
            scores.request_C = max(scores.request_C, 4)
        if isinstance(scores.G, int):
            scores.G = max(scores.G, 4)
        if isinstance(scores.R, int):
            scores.R = max(scores.R, 4)
        if scores.instructional is not None:
            from cre_rentroll.predict_followup import note_awaiting_followup

            note_awaiting_followup(turn_ctx.username(), bool(scores.instructional))
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
        hint = need_replan_hint(query, vault_label=None, prediction_clarify=clarify)
    elif scores.vault_calc_ok is False or min(g, c) <= 2:
        decision = "need_replan"
        hint = need_replan_hint(
            query,
            vault_label=vault_label if invented else None,
            prediction_clarify=clarify,
        )
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


def ask_prediction_agent(horizon_months: int = 12, question: str = "") -> str:
    """Call the capabilities gateway ask_prediction_agent for a forward outlook
    or floor-plan vacancy probability.

    When: forecast occupancy, lease expirations, MTM waterfall, rollover,
    loss-to-lease, or predicted income — also which floor plan is more
    likely vacant on this roll, or the probability a unit type is vacant.
    Also when THIS message is a plan name or renovated yes/no after that
    agent asked which group.
    When not: current occupancy or in-place rent (query_units / compute_income),
    map, weather.
    How: POST the one MCP tool ask_prediction_agent with
    this call's horizon_months and question. The gateway forwards to the vacancy
    app MCP ask_vacancy and attaches this deal's vault-roll plan table
    server-side, so the vacancy agent fits it transiently (never saved).
    Pass THIS user question. Do not invent a building.
    Never upload or train. Quote the vacancy agent's answer (predicted rate or
    instructions to the user) and stop this turn — do not call again with a
    guessed plan.
    If CAPABILITIES_GATEWAY_URL is unset, returns an error that the capabilities
    gateway is required.
    """
    from cre_rentroll.predict_followup import this_turn_predictor_result

    prior = this_turn_predictor_result()
    if prior:
        payload = {
            "ok": True,
            "source": "vacancy-prediction-agent",
            "answer": str(prior.get("answer") or "").strip(),
            "same_turn_stop": True,
        }
        return json.dumps(payload)
    return _capability(
        "ask_prediction_agent",
        {"horizon_months": int(horizon_months or 12), "question": (question or "").strip()},
    )


def _planless_guard(name: str, fn: Any) -> Any:
    """Planless turns run one routed work tool. A different work tool is
    rejected until update_plan seeds a checklist — same reject-then-hint
    shape as the consecutive update_plan_status guard. Same-tool repeats
    stay allowed (e.g. a filtered query_units after an unfiltered one)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        routed = turn_ctx.planless()
        if (
            routed
            and name in WORK_TOOL_NAMES
            and name != routed
            and not turn_ctx.checklist()
        ):
            err = {
                "ok": False,
                "rejected": True,
                "error": (
                    f"Planless turn — this ask was routed to {routed} only. "
                    f"{name} is extra work: call update_plan with the NEW "
                    "numbered remaining list first, then this tool."
                ),
            }
            turn_ctx.record(name, dict(kwargs), err)
            return json.dumps(err)
        return fn(*args, **kwargs)

    return wrapper


TOOL_IMPLS: dict[str, Any] = {
    "create_plan": create_plan,
    "update_plan": update_plan,
    "update_plan_status": update_plan_status,
    "reflection": reflection,
    "score_gcr": score_gcr,
    "query_units": _planless_guard("query_units", query_units),
    "compute_income": _planless_guard("compute_income", compute_income),
    "compute_noi": _planless_guard("compute_noi", compute_noi),
    "compute_cap_rate": _planless_guard("compute_cap_rate", compute_cap_rate),
    "osm_geocode": _planless_guard("osm_geocode", osm_geocode),
    "web_search": _planless_guard("web_search", web_search),
    "ask_prediction_agent": _planless_guard("ask_prediction_agent", ask_prediction_agent),
}
