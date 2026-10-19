from __future__ import annotations

import inspect
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from contextvars import copy_context
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cre_rentroll import tools, turn_ctx
from cre_rentroll.run_events import emit
from cre_rentroll.scope import NEED_FILE_TEXT, OOS_TEXT
from cre_rentroll.settings import openai_client_args

RATE_LIMIT_TEXT = (
    "The model is busy right now. Wait a minute and send again — "
    "this app did not keep retrying."
)


class RateLimitedError(RuntimeError):
    def __init__(self, detail: str = RATE_LIMIT_TEXT) -> None:
        super().__init__(detail)


def is_rate_limited(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitedError):
        return True
    if type(exc).__name__ in {"RateLimitError", "APIStatusError"}:
        status = getattr(exc, "status_code", None)
        if status == 429 or type(exc).__name__ == "RateLimitError":
            return True
    text = str(exc).lower()
    if "429" in text or "rate limit" in text or "rate_limit" in text:
        return True
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None) == 429


def _skills_dir() -> Path:
    env = os.environ.get("CRE_SKILLS_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for cand in (here.parents[1] / "skills", here.parents[2] / "skills"):
        if cand.is_dir():
            return cand
    return here.parents[1] / "skills"


SKILLS_DIR = _skills_dir()

InventoryName = str

REACT_TOOLS = (
    "create_plan",
    "update_plan",
    "update_plan_status",
    "reflection",
    "score_gcr",
)
BOOK_TOOLS = (
    *REACT_TOOLS,
    "query_units",
    "compute_income",
    "compute_noi",
    "compute_cap_rate",
    "osm_geocode",
    "web_search",
    "publish_memo",
)
BOOK_SKILLS = ("rent-roll-abstract", "citation-policy", "underwriting")


@dataclass(frozen=True)
class Inventory:
    name: InventoryName
    tool_names: tuple[str, ...]
    skills: tuple[str, ...]
    structured: str = ""


BOOK = Inventory("DealRentroll", BOOK_TOOLS, BOOK_SKILLS)
INVENTORIES = {"DealRentroll": BOOK}


def load_skill_text(names: tuple[str, ...]) -> str:
    try:
        from cre_rentroll.agentcore_platform import load_registry_skills

        registered = load_registry_skills(names)
        if registered.strip():
            return registered
    except Exception:
        pass
    chunks: list[str] = []
    for name in names:
        path = SKILLS_DIR / name / "SKILL.md"
        if path.is_file():
            chunks.append(f"### skill:{name}\n{path.read_text(encoding='utf-8')}")
    text = "\n\n".join(chunks)
    try:
        from cre_rentroll.agentcore_platform import note_disk_skills

        note_disk_skills(names, bool(text.strip()))
    except Exception:
        pass
    return text


def system_prompt(inv: Inventory, facts: str) -> str:
    skills = load_skill_text(inv.skills)
    vault_has = "VAULT HAS" in (facts or "")
    if vault_has:
        file_scope = (
            "The rent roll is already ingested (VAULT HAS). Never ask the user to "
            "attach a file. When VAULT HAS: never emit the out-of-scope refuse "
            "sentence (I only answer questions about an uploaded rent roll). "
            "Buy / worth / should I acquire / value is in-scope underwriting — "
            "call create_plan then compute_income and compute_noi (opex optional). "
            "If opex or ask price is missing, mark value/buy [incomplete], say what "
            "the roll supports (units, occupancy, in-place income), and do not refuse. "
            "Out-of-scope remains only weather, sports, recipes, or small talk. "
            "Locate / map / where is this property: never pass a "
            "spreadsheet filename or 'Rent Roll' title to osm_geocode. Use FACT# "
            "address_hint if it is a street/city. If geo_confidence is low or the "
            "hint is empty, web_search for a street address that matches unit_count, "
            "then osm_geocode that street string. If OSM has no hits, mark locate "
            "[incomplete] and say you cannot place it on a map — do not loop.\n"
        )
    else:
        file_scope = (
            "If the ask is in-scope CRE (including locate/map) but the prompt says VAULT "
            "EMPTY (no ingested roll), ask for a file with this exact text and do not call "
            "query_units / compute_* / osm_geocode / web_search — there is no property name "
            "or address until a roll is attached:\n"
            f"{NEED_FILE_TEXT}\n"
        )
    return (
        "You are the CRE Deal Rentroll agent (one ReAct loop, full tool menu). "
        "Intent is your first create_plan checklist — Python does not pick a specialist. "
        "Python does not start a second run_agent if the draft is empty.\n"
        "Map / locate / where is this property / put it on a "
        "map is in-scope CRE (same as units, occupancy, income, underwriting) — never "
        "the out-of-scope refuse. Buy / worth / should I acquire / value is in-scope "
        "underwriting even with no opex or ask price — never the out-of-scope refuse. "
        "Weather, sports, recipes, small talk, or other non-deal chat: answer without "
        "tools using this exact text:\n"
        f"{OOS_TEXT}\n"
        "User-facing sentences are prose only. Never copy (skill), (tool), (MCP), or "
        "(LLM) into the user sentence — those tags are operator logs, not the answer.\n"
        f"{file_scope}"
        "FIRST action: always call create_plan ONCE listing EVERY distinct ask in THIS user "
        "question (map AND vacant/occupancy/units/income are separate numbered lines if both "
        "were asked — never 'osm_geocode, query_units' as one item). "
        "Never skip create_plan on in-scope CRE — one work tool is still a one-item checklist. "
        "One work tool per numbered line. Tag the current step [in_progress] and the rest "
        "[pending]. Do not call create_plan again. "
        "Locate / map / where is this property with NO other CRE ask → one osm_geocode item only, "
        "then score_gcr with the map sentence. Do not add query_units on a map-only question. "
        "If the same message also asks vacant, occupancy, units, or income, those MUST be "
        "create_plan items from the start (osm_geocode + query_units, not map-only). "
        "Never call a work tool that is not on the checklist. If you discover extra work after "
        "create_plan, call update_plan with the NEW remaining work FIRST, then that tool. "
        "osm_geocode query is the street address from FACT#/address_hint or the roll — never "
        "the unit_count (do not geocode 'La Cordoba, 164' when 164 is unit count). "
        "Do not pad with abstract or underwrite steps unless the user asked for those numbers. "
        "Weather / sports / recipes / small talk: no create_plan and no other tools.\n"
        "Then call the ONE in_progress / next pending work tool (query_units, compute_income, "
        "compute_noi, compute_cap_rate, osm_geocode, web_search, or publish_memo). After that vault "
        "tool, call update_plan_status with the SAME items: mark that step [completed] and the next "
        "[in_progress] — do not replace the list with a new one-liner and do not resend every "
        "line as [pending]. If that tool cannot supply the asked result (missing data, empty "
        "or error payload, field not in the vault), mark the item [incomplete]. [incomplete] "
        "is terminal like [completed] but without an answer — do not retry that item. After "
        "update_plan_status, if any item is [pending] or [in_progress], the "
        "NEXT action MUST be that work tool — not update_plan_status. Call update_plan_status only immediately "
        "after a real vault/search/geocode/memo tool (or after a reflection that changed statuses). "
        "Forbidden: update_plan_status → update_plan_status except to mark [completed] or [incomplete] a work "
        "tool that already ran this run. If remaining items are [completed] or [incomplete], "
        "do not call those tools again — Next: score_gcr then answer. You may call reflection "
        "ONCE as a SELF-CHECK after the vault tool the question needed. When the "
        "checklist is done, write the user-facing sentence in prose "
        "(vacant-only is allowed; do not require RollAbstract JSON). "
        "Forbidden: text-only end_turn on a CRE/rent-roll answer (a sentence with stop/end_turn "
        "and no tool). Before you stop you MUST call score_gcr(draft) with that sentence. "
        "Do not stop with a sentence and no score_gcr. Do not return to the user "
        "until G/C are ok or you hit the turn limit. If score_gcr says need_replan "
        "(min(G,C)<=2), call update_plan with the NEW remaining work only (not create_plan, "
        "not status-only on the failed steps). [completed] and [incomplete] rows stay on the "
        "list automatically — do not drop them. Then those work tools, then "
        "score_gcr again — never invent query_units/income/NOI for a locate-only ask. "
        "Do not call score_gcr after every MCP hit. Do not call reflection after every "
        "tool — that burns the turn limit. Do not loop create_plan + update_plan + reflection with "
        "no vault work until the turn cap. Do not loop need_replan until turn 12.\n"
        "If the checklist still has [pending]/[in_progress] work a tool can still do, set "
        "need_replan and call update_plan_status on those items; then pick the "
        "missing tool yourself — once, not until the turn cap. "
        "If the steps themselves are wrong, call update_plan with the new list. "
        "Do not wait for Python to replan or inject tools.\n"
        "If web_search fails (search down / forced failure), do not call web_search again. "
        "Mark that search item [incomplete], then YOU write a short user-facing sentence "
        "that metro news is unavailable and call score_gcr with that sentence. "
        "Never copy Need replan / update_plan / update_plan_status / tool-return text "
        "into the user sentence — Python does not write that answer for you.\n"
        "Still use vault MCP tools for numbers. Match the tool to the question: "
        "vacant-only → query_units only (do not require compute_income); "
        "occupancy/income → compute_income (and query_units if needed). "
        "in_place_monthly is occupied units only. A follow-up asking what in-place "
        "monthly would be if all units were occupied / 100% occupancy / no vacancy "
        "is underwriting, not a repeat of in_place_monthly. Call compute_income and "
        "use estimated_full_occupancy_monthly (or mean_occupied_rent × unit_count, "
        "or in_place_monthly / occupancy_pct, or in_place plus vacant asking rents "
        "from query_units when those rents are present). If they give a fill rent "
        "for vacant units, call compute_income(vacant_fill_rent=that monthly $) and "
        "cite scenario_full_occupancy_annual (in-place annual + vacant fill), not "
        "vacant fill alone. Do not answer a different "
        "question (occupancy counts) when the user says the dollar figure is the "
        "same as with vacancy. "
        "One unfiltered query_units JSON returns unit_count, occupied_count, vacant_count, "
        "occupancy_pct, and mtm_count (units on month-to-month leases) and a note that those "
        "headline counts are complete in that result. If several checklist intents are "
        "headline counts (units, occupancy, vacant, mtm_count), that is ONE unfiltered "
        "query_units item, not four. After an unfiltered query_units has run this turn, "
        "do not call query_units again for those counts. Extra query_units only to list a "
        "filtered subset of unit rows, not to re-fetch counts. compute_income still for "
        "in-place monthly $. compute_income JSON also includes mtm_count; "
        "use counts for existence/yes-no. query_units filters unit occupancy status "
        "only (Occupied/Vacant/all). "
        "Do not always call compute_income. Do not stop with an empty draft.\n"
        "Do not geocode or web_search on a pure rent-roll abstract (unit count, occupancy, "
        "income, vacant) unless the user asked to map or search.\n"
        "Do not put tenant names in answers (they were stripped at ingest).\n"
        "Cite vault totals, calculator JSON, OSM, and article URLs as appropriate.\n"
        f"{facts}\n\n{skills}"
    )


def _dump_jsonish(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value).strip()
    except (TypeError, ValueError):
        return str(value).strip()


def _block_text(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        text = item.get("text")
        if text:
            return str(text)
        for key in ("json", "structuredContent", "structured_output"):
            dumped = _dump_jsonish(item.get(key))
            if dumped:
                return dumped
        for key in ("toolResult", "tool_result", "result"):
            inner = item.get(key)
            if inner is not None and inner is not item:
                got = _block_text(inner)
                if got:
                    return got
        content = item.get("content")
        if content is not None and content is not item:
            got = _content_text(content)
            if got:
                return got
        citations = item.get("citationsContent") or {}
        if isinstance(citations, dict):
            inner = citations.get("content") or []
            return "\n".join(p for p in (_block_text(c) for c in inner) if p)
        return ""
    text = getattr(item, "text", None)
    if text:
        return str(text)
    for attr in ("toolResult", "tool_result", "json"):
        inner = getattr(item, attr, None)
        if inner is not None:
            got = _block_text(inner)
            if got:
                return got
    return ""


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(p for p in (_block_text(item) for item in content) if p).strip()
    return _block_text(content).strip()


def _message_text(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, dict):
        return _content_text(message.get("content"))
    return _content_text(getattr(message, "content", None))


def _messages_text(messages: Any) -> str:
    if not messages:
        return ""
    assistant: list[str] = []
    other: list[str] = []
    for msg in messages:
        role = None
        if isinstance(msg, dict):
            role = msg.get("role")
        else:
            role = getattr(msg, "role", None)
        text = _message_text(msg)
        if not text:
            continue
        if role in (None, "assistant"):
            assistant.append(text)
        else:
            other.append(text)
    if assistant:
        return "\n".join(assistant).strip()
    return "\n".join(other).strip()


def draft_from_strands(result: Any, messages: Any = None) -> str:
    """Turn a Strands AgentResult (or lookalike) into the intern draft sentence.

    Chat always returns a sentence. Do not read AgentResult.structured_output.
    Prefer assistant message text; fall back to str(result) when that is the draft.
    """
    if result is None:
        emit("error", "Strands returned no result object.")
        return ""

    extra = _message_text(getattr(result, "message", None))
    if not extra:
        extra = _messages_text(messages if messages is not None else getattr(result, "messages", None))

    if not extra:
        raw = str(result).strip()
        # Dataclass/object repr is not the agent draft.
        if raw and not raw.startswith("AgentResult") and raw not in {"None", ""}:
            extra = raw

    if extra:
        return extra

    stop = getattr(result, "stop_reason", None)
    detail = "Strands returned no assistant message text"
    if stop:
        detail += f" (stop_reason={stop})"
    emit("error", f"{detail}.")
    return ""


def _strands_tools(names: tuple[str, ...]) -> list[Any]:
    """Book tools are MCP server implementations, invoked in-process (not stdio).

    `book_mcp_impls` keeps the original Python/FastMCP signatures (create_plan(tasks),
    query_units(occupancy, vacant_only), …). Do not wrap *args/**kwargs — Strands
    builds Pydantic from inspect.signature and would then require args+kwargs.
    """
    from strands import tool as strands_tool

    from cre_rentroll.mcp_runtime import book_mcp_impls

    mapping = book_mcp_impls()
    wrapped = []
    for name in names:
        fn = mapping[name]
        wrapped.append(strands_tool(name=name, description=inspect.getdoc(fn) or name)(fn))
    return wrapped


_SKIP_ARG_KEYS = frozenset(
    {
        "tenant",
        "tenant_name",
        "tenants",
        "units",
        "rows",
        "grid",
        "unit_grid",
        "pii",
        "names",
    }
)


def _log_arg_value(val: Any) -> Any:
    """Keep operator args intact; summarize only unit grids / nested row dumps."""
    if isinstance(val, (list, tuple)):
        if val and all(isinstance(x, dict) for x in val):
            return f"[{len(val)}]"
        return [_log_arg_value(x) for x in val]
    if isinstance(val, dict):
        return _clean_args_dict(val)
    if isinstance(val, str):
        return re.sub(r"\s+", " ", val).strip()
    return val


def _clean_args_dict(parsed: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, val in parsed.items():
        lk = str(key).lower()
        if lk in _SKIP_ARG_KEYS or "tenant" in lk:
            continue
        cleaned[key] = _log_arg_value(val)
    return cleaned


def _short_tool_args(args: Any) -> str:
    """Operator-facing tool args in full — skip unit grids and tenant names only."""
    parsed: Any = args
    if isinstance(args, str):
        blob = args.strip()
        if not blob:
            return ""
        try:
            parsed = json.loads(blob)
        except (json.JSONDecodeError, TypeError, ValueError):
            return re.sub(r"\s+", " ", blob).strip()
    if isinstance(parsed, dict):
        cleaned = _clean_args_dict(parsed)
        try:
            blob = json.dumps(cleaned, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            blob = str(cleaned)
    elif parsed is None:
        return ""
    else:
        blob = json.dumps(_log_arg_value(parsed), default=str, ensure_ascii=False)
    blob = re.sub(r"\s+", " ", blob).strip()
    if not blob or blob in {"{}", "[]", '""'}:
        return ""
    return blob


def _tools_from_content(content: Any) -> tuple[list[str], str]:
    names: list[str] = []
    note = ""
    blocks = content if isinstance(content, list) else [content] if content else []
    for block in blocks:
        tu = None
        if isinstance(block, dict):
            tu = block.get("toolUse") or block.get("tool_use")
            if not tu and block.get("type") in {"tool_use", "toolUse"}:
                tu = block
        else:
            tu = getattr(block, "toolUse", None) or getattr(block, "tool_use", None)
        if not tu:
            continue
        if not isinstance(tu, dict):
            name = getattr(tu, "name", None)
            inp = getattr(tu, "input", None) or getattr(tu, "arguments", None)
        else:
            name = tu.get("name")
            inp = tu.get("input") or tu.get("arguments")
        if name:
            names.append(str(name))
            if not note and inp is not None:
                note = _short_tool_args(inp)
    return names, note


def _tools_from_message(message: Any) -> tuple[list[str], str]:
    if message is None:
        return [], ""
    if isinstance(message, dict):
        tcs = message.get("tool_calls")
        content = message.get("content")
    else:
        tcs = getattr(message, "tool_calls", None)
        content = getattr(message, "content", None)
    names: list[str] = []
    note = ""
    for tc in tcs or []:
        fn = getattr(tc, "function", None) if not isinstance(tc, dict) else tc.get("function")
        if isinstance(fn, dict):
            name, raw = fn.get("name"), fn.get("arguments")
        elif fn is not None:
            name, raw = getattr(fn, "name", None), getattr(fn, "arguments", None)
        else:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            raw = None
        if name:
            names.append(str(name))
            if not note and raw is not None:
                note = _short_tool_args(raw)
    from_blocks, block_note = _tools_from_content(content)
    for name in from_blocks:
        if name not in names:
            names.append(name)
    return names, note or block_note


def _parse_chat_completion(resp: Any) -> tuple[list[str], str, str | None]:
    choices = getattr(resp, "choices", None)
    if choices is None and isinstance(resp, dict):
        choices = resp.get("choices")
    if not choices:
        return [], "", None
    ch0 = choices[0]
    stop = getattr(ch0, "finish_reason", None)
    if stop is None and isinstance(ch0, dict):
        stop = ch0.get("finish_reason")
    msg = getattr(ch0, "message", None)
    if msg is None and isinstance(ch0, dict):
        msg = ch0.get("message")
    names, note = _tools_from_message(msg)
    if stop == "tool_calls":
        stop = "tool_use"
    elif stop == "stop":
        stop = "end_turn"
    return names, note, str(stop) if stop else None


def _emit_react_turn(
    n: int,
    tool_names: list[str] | None = None,
    args_note: str = "",
    stop: str | None = None,
) -> None:
    names = [str(x) for x in (tool_names or []) if x]
    if names:
        msg = f"ReAct turn {n} — {', '.join(names)}"
        if args_note:
            msg += f" {args_note}"
    else:
        msg = f"ReAct turn {n} — text-only"
    if stop:
        msg += f" stop={stop}"
    emit("react", msg, llm=True, turn=n, tools=names or None, stop=stop)


class _ReactTurnLog:
    """One operator line per completed ReAct model message (not per token)."""

    def __init__(self) -> None:
        self.n = 0
        self.phase = "react"
        self._wrapped = False
        self._cycle_open = False
        self._pending_tools: list[str] = []
        self._pending_args = ""

    def mark_wrapped(self) -> None:
        self._wrapped = True

    def on_create_result(self, resp: Any) -> None:
        if self.phase != "react":
            return
        names, note, stop = _parse_chat_completion(resp)
        self._emit(names, note, stop)
        self._cycle_open = False
        self._pending_tools = []
        self._pending_args = ""

    def callback(self, **kwargs: Any) -> None:
        if self.phase != "react" or self._wrapped:
            return
        if kwargs.get("data") is not None and "message" not in kwargs and not kwargs.get("complete"):
            return
        if kwargs.get("init_event_loop"):
            return
        if kwargs.get("start_event_loop") or kwargs.get("start"):
            self._cycle_open = True
        ctu = kwargs.get("current_tool_use")
        if isinstance(ctu, dict) and ctu.get("name"):
            name = str(ctu["name"])
            if name not in self._pending_tools:
                self._pending_tools.append(name)
            inp = ctu.get("input") if "input" in ctu else ctu.get("arguments")
            if inp not in (None, "", {}):
                self._pending_args = _short_tool_args(inp)
            if "message" not in kwargs and not kwargs.get("complete"):
                return
        msg = kwargs.get("message")
        if msg is not None:
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            if role in (None, "assistant"):
                names, note = _tools_from_message(msg)
                if not names:
                    names = list(self._pending_tools)
                stop = kwargs.get("stop_reason")
                if isinstance(msg, dict):
                    stop = stop or msg.get("stop_reason")
                self._emit(names, note or self._pending_args, str(stop) if stop else None)
                self._cycle_open = False
                self._pending_tools = []
                self._pending_args = ""
                return
        stop = None
        if kwargs.get("force_stop"):
            stop = str(kwargs.get("force_stop_reason") or "force_stop")
        result = kwargs.get("result")
        if result is not None:
            stop = stop or getattr(result, "stop_reason", None)
            if stop is not None:
                stop = str(stop)
        if stop and self._cycle_open:
            self._emit(list(self._pending_tools), self._pending_args, stop)
            self._cycle_open = False
            self._pending_tools = []
            self._pending_args = ""

    def _emit(self, names: list[str], args_note: str, stop: str | None) -> None:
        self.n += 1
        _emit_react_turn(self.n, names, args_note, stop)


def _looks_chat_stream(result: Any) -> bool:
    if result is None:
        return False
    choices = getattr(result, "choices", None)
    if choices is None and isinstance(result, dict):
        choices = result.get("choices")
    if choices:
        return False
    if hasattr(result, "__aiter__"):
        return True
    return hasattr(result, "__iter__") and not isinstance(result, (str, bytes, dict, list, tuple))


def _after_openai_create(
    result: Any,
    kind: str,
    on_result: Callable[[Any], None] | None,
) -> Any:
    """Record prompt/completion tokens; keep streams iterable for Strands."""
    if _looks_chat_stream(result):
        if hasattr(result, "__aiter__"):

            async def agen() -> Any:
                last_usage = None
                async for chunk in result:
                    pin, pout = turn_ctx.parse_openai_usage(chunk)
                    if pin is not None and pout is not None:
                        last_usage = chunk
                    yield chunk
                if last_usage is not None:
                    turn_ctx.add_llm_from_response(kind, last_usage)

            return agen()

        def gen() -> Any:
            last_usage = None
            for chunk in result:
                pin, pout = turn_ctx.parse_openai_usage(chunk)
                if pin is not None and pout is not None:
                    last_usage = chunk
                yield chunk
            if last_usage is not None:
                turn_ctx.add_llm_from_response(kind, last_usage)

        return gen()

    turn_ctx.add_llm_from_response(kind, result)
    if on_result is not None:
        on_result(result)
    return result


def _wrap_completions_create(
    client: Any,
    kind: str,
    on_result: Callable[[Any], None] | None = None,
) -> bool:
    completions = getattr(getattr(client, "chat", None), "completions", None)
    orig = getattr(completions, "create", None)
    if orig is None:
        return False
    if getattr(orig, "_cre_llm_wrapped", False):
        return True

    if inspect.iscoroutinefunction(orig):

        async def acreate(*args: Any, **kwargs: Any) -> Any:
            result = await orig(*args, **kwargs)
            return _after_openai_create(result, kind, on_result)

        acreate._cre_llm_wrapped = True  # type: ignore[attr-defined]
        completions.create = acreate
        return True

    def create(*args: Any, **kwargs: Any) -> Any:
        result = orig(*args, **kwargs)
        if inspect.isawaitable(result):

            async def await_create() -> Any:
                return _after_openai_create(await result, kind, on_result)

            return await_create()
        return _after_openai_create(result, kind, on_result)

    create._cre_llm_wrapped = True  # type: ignore[attr-defined]
    completions.create = create
    return True


def _hook_openai_create(
    model: Any,
    kind: str,
    on_result: Callable[[Any], None] | None = None,
) -> bool:
    """Count each chat.completions.create round on a Strands OpenAI model."""
    hooked = False
    for attr in ("client", "_client", "openai_client", "_custom_client", "async_client"):
        client = getattr(model, attr, None)
        if client is None:
            continue
        if _wrap_completions_create(client, kind, on_result):
            hooked = True

    get_client = getattr(model, "_get_client", None)
    if callable(get_client) and not getattr(get_client, "_cre_llm_wrapped", False):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def wrapped_get(*args: Any, **kwargs: Any) -> Any:
            async with get_client(*args, **kwargs) as client:
                _wrap_completions_create(client, kind, on_result)
                yield client

        wrapped_get._cre_llm_wrapped = True  # type: ignore[attr-defined]
        model._get_client = wrapped_get
        hooked = True
    return hooked


def _react_text(result: Any, messages: Any) -> str:
    extra = _message_text(getattr(result, "message", None) if result is not None else None)
    if not extra:
        extra = _messages_text(messages if messages is not None else getattr(result, "messages", None))
    if extra:
        return extra
    raw = str(result).strip() if result is not None else ""
    if raw and not raw.startswith("AgentResult") and raw not in {"None", ""}:
        return raw
    return ""


def run_strands(prompt: str, inv: Inventory, facts: str) -> str:
    from strands import Agent
    from strands.models.openai import OpenAIModel

    model = OpenAIModel(
        client_args=openai_client_args(),
        model_id=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    )
    tracker = _ReactTurnLog()
    hooked = _hook_openai_create(model, "agent", on_result=tracker.on_create_result)
    agent = Agent(
        model=model,
        system_prompt=system_prompt(inv, facts),
        tools=_strands_tools(inv.tool_names),
        callback_handler=tracker.callback,
    )
    live = getattr(agent, "model", model)
    hooked = _hook_openai_create(live, "agent", on_result=tracker.on_create_result) or hooked
    # Per-request AsyncOpenAI (_get_client) still needs callback react lines.
    if hooked and not callable(getattr(live, "_get_client", None)):
        tracker.mark_wrapped()
    started = time.monotonic()
    budget = 32.0

    def _timed(fn: Callable[[], Any]) -> Any:
        left = budget - (time.monotonic() - started)
        if left <= 0.5:
            raise FuturesTimeout()
        ctx = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(ctx.run, fn).result(timeout=left)

    def _note_unwrapped(before_n: int, *, turns: int = 0) -> None:
        if turn_ctx.llm_call_count() != before_n:
            return
        # Mocked Agent() or a client we could not wrap.
        n = turns if turns > 0 else 1
        for _ in range(n):
            turn_ctx.add_llm("agent")

    def _react():
        from cre_rentroll.mcp_runtime import bind_turn_context, clear_turn_context

        # ReAct tools-only. Do not pass structured_output_model: a schema
        # starves ReAct (limit_turns, zero tools). G/C/R is score_gcr in-loop.
        bind_turn_context()
        try:
            return agent(prompt, limits={"turns": 12})
        finally:
            clear_turn_context()

    before = turn_ctx.llm_call_count()
    try:
        result = _timed(_react)
    except FuturesTimeout as exc:
        raise RateLimitedError(
            "The model took too long. Wait a minute and send again — "
            "this app did not keep retrying."
        ) from exc
    except Exception as exc:
        if is_rate_limited(exc):
            raise RateLimitedError() from exc
        raise
    _note_unwrapped(before, turns=tracker.n)
    messages = getattr(agent, "messages", None)
    return draft_from_strands(result, messages=messages) or _react_text(result, messages)


def run_openai_tools(prompt: str, inv: Inventory, facts: str) -> str:
    """Fallback ReAct-style loop if Strands import fails. Same tools / step cap."""
    from openai import OpenAI

    client = OpenAI(**openai_client_args())
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    spec = _openai_specs(inv.tool_names)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(inv, facts)},
        {"role": "user", "content": prompt},
    ]
    from cre_rentroll.mcp_runtime import bind_turn_context, book_mcp_impls, clear_turn_context

    impls = book_mcp_impls()
    bind_turn_context()
    try:
        return _run_openai_tools_loop(client, model, spec, messages, impls)
    finally:
        clear_turn_context()


def _run_openai_tools_loop(
    client: Any,
    model: str,
    spec: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    impls: dict[str, Callable[..., str]],
) -> str:
    import json

    react_text = ""
    for turn_i in range(1, 13):
        try:
            resp = client.chat.completions.create(model=model, messages=messages, tools=spec, tool_choice="auto")
            turn_ctx.add_llm_from_response("agent", resp)
        except Exception as exc:
            if is_rate_limited(exc):
                raise RateLimitedError() from exc
            raise
        names, args_note, stop = _parse_chat_completion(resp)
        _emit_react_turn(turn_i, names, args_note, stop)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            react_text = msg.content or ""
            messages.append({"role": "assistant", "content": react_text})
            break
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
            }
        )
        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            impl = impls[name]
            try:
                out = impl(**args)
            except Exception as exc:
                payload = {"ok": False, "error": str(exc)}
                traces_now = turn_ctx.traces()
                last = traces_now[-1] if traces_now else None
                already = (
                    last
                    and last.get("name") == name
                    and isinstance(last.get("result"), dict)
                    and last["result"].get("ok") is False
                )
                if not already:
                    turn_ctx.record(name, args, payload)
                out = json.dumps(payload)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(out)})
    else:
        react_text = "Stopped: max_steps (12)."
    return react_text


def _openai_specs(names: tuple[str, ...]) -> list[dict[str, Any]]:
    props = {
        "create_plan": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "string",
                    "description": (
                        "Always FIRST on in-scope CRE, including a single web_search. "
                        "Call ONCE at the start. One work tool per numbered line "
                        "(1. osm_geocode  2. query_units) — never comma-join two tools. "
                        "Map+vacant needs two lines. Map-only → one osm_geocode line. "
                        "Tag the current step [in_progress], the rest [pending]. "
                        "Do not call again — update_plan_status for statuses; "
                        "update_plan to replace the list on replan. "
                        "If several checklist intents are headline counts (units, occupancy, "
                        "vacant, mtm_count), that is ONE unfiltered query_units item, not four. "
                        "After an unfiltered query_units this turn, do not call query_units "
                        "again for those counts. compute_income still for in-place monthly $."
                    ),
                }
            },
            "required": ["tasks"],
        },
        "update_plan": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "string",
                    "description": (
                        "On need_replan, send NEW remaining work only (wrong metro/query, "
                        "failed search). Same shape as create_plan. Completed/incomplete "
                        "rows are kept automatically. Never a second create_plan. "
                        "Status-only is update_plan_status."
                    ),
                }
            },
            "required": ["tasks"],
        },
        "update_plan_status": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "string",
                    "description": (
                        "Same items as create_plan with updated [pending], [in_progress], "
                        "[completed], or [incomplete] tags (also [x], done, **in_progress**, "
                        "incomplet). [incomplete] = could not answer (missing data / tool "
                        "gave no result); do not retry that item. Call only "
                        "immediately after a vault/search/geocode/memo tool (or a reflection "
                        "that changed statuses). Then call the next pending work tool — "
                        "never update_plan_status twice in a row unless that tool already succeeded "
                        "this run (then mark it [completed] or [incomplete]). If remaining "
                        "items are [completed] or [incomplete], Next: score_gcr then answer. "
                        "Must not replace the list with a new one-liner. Matching is by item "
                        "text; unmatched wholesale replacements are ignored. New list = update_plan."
                    ),
                }
            },
            "required": ["tasks"],
        },
        "reflection": {
            "type": "object",
            "properties": {
                "analysis": {"type": "string"},
                "need_replan": {"type": "boolean"},
            },
            "required": ["analysis"],
        },
        "score_gcr": {
            "type": "object",
            "properties": {
                "draft": {
                    "type": "string",
                    "description": (
                        "The exact user-facing sentence to score. Required before text-only "
                        "end_turn on a CRE/rent-roll answer. Forbidden to stop with a sentence "
                        "and no score_gcr."
                    ),
                }
            },
            "required": ["draft"],
        },
        "query_units": {
            "type": "object",
            "properties": {
                "occupancy": {
                    "type": "string",
                    "description": (
                        "Occupied, Vacant, or all (omit or all = unfiltered). "
                        "Unfiltered JSON already includes unit_count, occupied_count, "
                        "vacant_count, occupancy_pct, mtm_count, and a note that those "
                        "headline counts are complete in this result. Filter only to LIST "
                        "a subset of unit rows — not to re-fetch counts, not lease type "
                        "(never MTM). After an unfiltered call this turn, do not call "
                        "query_units again for those counts."
                    ),
                },
                "vacant_only": {"type": "boolean"},
            },
        },
        "compute_income": {
            "type": "object",
            "properties": {
                "vacant_fill_rent": {
                    "type": "number",
                    "description": (
                        "Monthly $ the user stated to fill each vacant unit. "
                        "Returns scenario_full_occupancy_annual = in_place_annual + "
                        "vacant_count × rent × 12. Omit for vault/asking full occupancy."
                    ),
                }
            },
        },
        "compute_noi": {
            "type": "object",
            "properties": {"operating_expenses": {"type": "number"}},
        },
        "compute_cap_rate": {
            "type": "object",
            "properties": {
                "ask_price": {"type": "number"},
                "noi": {"type": "number"},
            },
            "required": ["ask_price"],
        },
        "osm_geocode": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Street, city, or property trade name. Never a rent-roll "
                        "filename (e.g. 'La Cordoba Rent Roll 04-20-21')."
                    ),
                }
            },
            "required": ["query"],
        },
        "web_search": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
        "publish_memo": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            "required": ["title", "body"],
        },
    }
    return [
        {"type": "function", "function": {"name": n, "description": tools.TOOL_IMPLS[n].__doc__ or n, "parameters": props[n]}}
        for n in names
    ]


def run_agent(prompt: str, inv: Inventory, facts: str) -> str:
    try:
        import strands  # noqa: F401
    except ImportError:
        return run_openai_tools(prompt, inv, facts)
    return run_strands(prompt, inv, facts)
