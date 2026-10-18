from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any

_traces: ContextVar[list[dict[str, Any]] | None] = ContextVar("cre_traces", default=None)
_username: ContextVar[str | None] = ContextVar("cre_username", default=None)
_hitl_allow: ContextVar[bool] = ContextVar("cre_hitl_allow", default=False)
_search_fail: ContextVar[bool] = ContextVar("cre_search_fail", default=False)
_asked_as_of: ContextVar[str | None] = ContextVar("cre_asked_as_of", default=None)
_run_t0: ContextVar[float | None] = ContextVar("cre_run_t0", default=None)
_retry_n: ContextVar[int] = ContextVar("cre_retry_n", default=0)
_plan_skills: ContextVar[list[str]] = ContextVar("cre_plan_skills", default=[])
_plan_inventories: ContextVar[list[str]] = ContextVar("cre_plan_inventories", default=[])
_llm_kinds: ContextVar[list[str]] = ContextVar("cre_llm_kinds", default=[])
_llm_plan: ContextVar[str | None] = ContextVar("cre_llm_plan", default=None)
_llm_calls: ContextVar[list[str]] = ContextVar("cre_llm_calls", default=[])
_llm_usage: ContextVar[list[tuple[int | None, int | None]]] = ContextVar("cre_llm_usage", default=[])
_checklist: ContextVar[list[dict[str, str]]] = ContextVar("cre_checklist", default=[])
_eval_scores: ContextVar[Any] = ContextVar("cre_eval_scores", default=None)
_eval_query: ContextVar[str | None] = ContextVar("cre_eval_query", default=None)


def reset_turn(
    username: str,
    *,
    hitl_allow: bool = False,
    search_fail: bool = False,
    keep_run: bool = False,
) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    _traces.set(traces)
    _username.set(username)
    _hitl_allow.set(hitl_allow)
    _search_fail.set(search_fail)
    _asked_as_of.set(None)
    _llm_kinds.set([])
    _llm_plan.set(None)
    _checklist.set([])
    _eval_scores.set({"value": None})
    _eval_query.set(None)
    if not keep_run:
        _run_t0.set(time.time())
        _retry_n.set(0)
        _plan_skills.set([])
        _plan_inventories.set([])
        _llm_calls.set([])
        _llm_usage.set([])
    return traces


def bump_retry() -> None:
    _retry_n.set(int(_retry_n.get() or 0) + 1)


def retry_count() -> int:
    return int(_retry_n.get() or 0)


def run_started() -> float | None:
    return _run_t0.get()


def set_plan(inventories: list[str], skills: list[str]) -> None:
    _plan_inventories.set(list(inventories))
    _plan_skills.set(list(skills))


def plan_skills() -> list[str]:
    return list(_plan_skills.get() or [])


def plan_inventories() -> list[str]:
    return list(_plan_inventories.get() or [])


def parse_openai_usage(resp: Any) -> tuple[int | None, int | None]:
    """Read prompt/input and completion/output tokens from a Chat Completions result."""
    usage = getattr(resp, "usage", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage")
    if usage is None:
        return None, None

    def _as_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        return n if n >= 0 else None

    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
    else:
        prompt = getattr(usage, "prompt_tokens", None)
        if prompt is None:
            prompt = getattr(usage, "input_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        if completion is None:
            completion = getattr(usage, "output_tokens", None)
    return _as_int(prompt), _as_int(completion)


def add_llm(
    kind: str,
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    """Record one real model API round (scope / agent ReAct / judge)."""
    # Append in place so ThreadPoolExecutor copy_context() still sees counts.
    bucket = _llm_calls.get()
    if bucket is None:
        bucket = []
        _llm_calls.set(bucket)
    bucket.append(kind)
    usage = _llm_usage.get()
    if usage is None:
        usage = []
        _llm_usage.set(usage)
    usage.append((prompt_tokens, completion_tokens))


def add_llm_from_response(kind: str, resp: Any) -> None:
    pin, pout = parse_openai_usage(resp)
    add_llm(kind, prompt_tokens=pin, completion_tokens=pout)


def add_llm_if_unchanged(kind: str, before: int) -> None:
    """Mocks skip inner completions.create — still count this kind.

    ``before`` is the count of this kind (not the total LLM count), so a
    judge round inside agent() does not suppress the agent round.
    """
    if llm_call_kinds().count(kind) == before:
        add_llm(kind)


def llm_call_count() -> int:
    return len(_llm_calls.get() or [])


def llm_call_kinds() -> list[str]:
    return list(_llm_calls.get() or [])


def llm_usage() -> list[tuple[int | None, int | None]]:
    return list(_llm_usage.get() or [])


def llm_token_summary() -> tuple[str, int | None, int | None]:
    """Operator bit plus totals. Unknown when any counted call lacks usage."""
    kinds = llm_call_kinds()
    usages = llm_usage()
    if not kinds:
        return "in 0 tok · out 0 tok", 0, 0
    if len(usages) != len(kinds):
        return "in/out unknown", None, None
    pins: list[int] = []
    pouts: list[int] = []
    for pin, pout in usages:
        if pin is None or pout is None:
            return "in/out unknown", None, None
        pins.append(pin)
        pouts.append(pout)
    total_in = sum(pins)
    total_out = sum(pouts)
    return f"in {total_in} tok · out {total_out} tok", total_in, total_out


def note_llm_kind(kind: str) -> None:
    bucket = list(_llm_kinds.get() or [])
    bucket.append(kind)
    _llm_kinds.set(bucket)


def llm_kinds() -> list[str]:
    return list(_llm_kinds.get() or [])


def llm_kind_ran(kind: str) -> bool:
    return kind in (_llm_kinds.get() or [])


def set_llm_plan(text: str) -> None:
    _llm_plan.set(text)


def llm_plan() -> str | None:
    return _llm_plan.get()


def checklist() -> list[dict[str, str]]:
    return list(_checklist.get() or [])


def set_checklist(items: list[dict[str, str]]) -> None:
    _checklist.set([{"text": str(i.get("text") or ""), "status": str(i.get("status") or "pending")} for i in items])


def set_eval_query(query: str | None) -> None:
    _eval_query.set(query)


def eval_query() -> str | None:
    return _eval_query.get()


def set_eval_scores(scores: Any) -> None:
    box = _eval_scores.get()
    if isinstance(box, dict) and "value" in box:
        box["value"] = scores
        return
    _eval_scores.set({"value": scores})


def eval_scores() -> Any:
    box = _eval_scores.get()
    if isinstance(box, dict) and "value" in box:
        return box["value"]
    return box


def set_asked_as_of(value: str | None) -> None:
    _asked_as_of.set(value)


def asked_as_of() -> str | None:
    return _asked_as_of.get()


def traces() -> list[dict[str, Any]]:
    return _traces.get() or []


def last_tool_name() -> str | None:
    bucket = _traces.get() or []
    if not bucket:
        return None
    name = str(bucket[-1].get("name") or "").strip()
    return name or None


def username() -> str | None:
    return _username.get()


def hitl_allowed() -> bool:
    return _hitl_allow.get()


def search_should_fail() -> bool:
    return _search_fail.get()


def record(name: str, args: dict[str, Any], result: Any) -> None:
    bucket = _traces.get()
    if bucket is None:
        return
    bucket.append({"name": name, "args": args, "result": result})
    from cre_rentroll.reflect import on_tool_error
    from cre_rentroll.run_events import emit, tool_message

    from_model = name in {
        "create_plan",
        "update_plan",
        "update_plan_status",
        "reflection",
        "score_gcr",
    }
    emit("tool", tool_message(name, args, result), tool=name, llm=from_model)
    if name in {"create_plan", "update_plan", "update_plan_status"}:
        note_llm_kind(name)
        changed = True
        log = ""
        if isinstance(result, dict):
            changed = result.get("changed", True)
            log = str(result.get("log") or "")
        if changed or log:
            emit("plan", log or tool_message(name, args, result), tool=name, llm=True)
    elif name == "reflection":
        note_llm_kind("reflection")
        emit("reflect", tool_message(name, args, result), tool=name, llm=True)
        need = bool((args or {}).get("need_replan"))
        if isinstance(result, dict):
            need = need or bool(result.get("need_replan"))
        if need:
            emit(
                "replan",
                "need_replan — call update_plan with NEW remaining work; "
                "completed rows stay (agent, not inventory)",
            )
    if isinstance(result, dict) and result.get("ok") is False and not result.get("rejected"):
        note = on_tool_error(name, str(result.get("error") or "failed"))
        emit("error", note)
        if name in ("web_search", "osm_geocode") or "replan" in note.lower():
            emit("replan", note)
