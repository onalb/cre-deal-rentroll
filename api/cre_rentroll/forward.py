"""One forwarder for every gateway tool the deal model calls.

tools/list supplies the name, description, and argument schema. This module
hides session fields, stamps them on the way out, and posts tools/call.
A tool is a plan tool when its schema has a checklist argument. The four
plan-tool names are not written here.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Callable

# Fields the model must not set. The forwarder fills the ones it knows.
HIDDEN = frozenset(
    {
        "route",
        "username",
        "checklist",
        "last_work_tool",
        "last_work_status",
        "ran_ok",
        "status_since_work",
        "confirm",
        "content_base64",
        "roll",
        "hitl_allow",
        "filename",
    }
)


def schema_properties(spec: dict[str, Any]) -> dict[str, Any]:
    schema = spec.get("input_schema") if isinstance(spec.get("input_schema"), dict) else {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    return props if isinstance(props, dict) else {}


def has_argument(spec: dict[str, Any], name: str) -> bool:
    return name in schema_properties(spec)


def visible_properties(spec: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in schema_properties(spec).items() if key not in HIDDEN}


def model_schema(spec: dict[str, Any]) -> dict[str, Any]:
    schema = spec.get("input_schema") if isinstance(spec.get("input_schema"), dict) else {}
    props = visible_properties(spec)
    required = [
        name
        for name in (schema.get("required") or [])
        if name in props
    ]
    out: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


def _plan_names(specs: list[dict[str, Any]]) -> set[str]:
    return {str(spec.get("name") or "") for spec in specs if has_argument(spec, "checklist")}


def _work_state(specs: list[dict[str, Any]], calling: str) -> dict[str, Any]:
    """Last work tool and whether this plan call repeats the previous one."""
    from cre_rentroll import turn_ctx

    plan = _plan_names(specs)
    last_tool = ""
    last_status = ""
    ran_ok: list[str] = []
    repeated = False
    for row in turn_ctx.traces() or []:
        name = str(row.get("name") or "")
        if not name or name == "score_gcr" or name in plan:
            if name == calling and name in plan:
                repeated = True
            elif name in plan:
                repeated = False
            continue
        last_tool = name
        result = row.get("result")
        ok = not (isinstance(result, dict) and result.get("ok") is False)
        last_status = "ok" if ok else "error"
        repeated = False
        if ok and name not in ran_ok:
            ran_ok.append(name)
    return {
        "last_work_tool": last_tool,
        "last_work_status": last_status if last_tool else "",
        "ran_ok": ran_ok,
        "status_since_work": repeated,
    }


def stamp_arguments(
    spec: dict[str, Any],
    model_args: dict[str, Any],
    *,
    route: str,
    username: str = "",
    as_of: str = "",
    checklist: list | None = None,
    work: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Model args plus the hidden fields this tool's schema declares."""
    props = schema_properties(spec)
    clean = {
        key: value
        for key, value in model_args.items()
        if key in props and key not in HIDDEN and value is not None
    }
    if "route" in props:
        clean["route"] = route
    if "username" in props and username:
        clean["username"] = username
    if "as_of" in props and as_of and not clean.get("as_of"):
        clean["as_of"] = as_of
    if "question" in props and not str(clean.get("question") or "").strip():
        from cre_rentroll import turn_ctx

        asked = (turn_ctx.eval_query() or "").strip()
        if asked:
            clean["question"] = asked
    if "checklist" in props:
        clean["checklist"] = list(checklist or [])
        state = work or {}
        if "last_work_tool" in props:
            clean["last_work_tool"] = state.get("last_work_tool") or ""
        if "last_work_status" in props:
            clean["last_work_status"] = state.get("last_work_status") or ""
        if "ran_ok" in props:
            clean["ran_ok"] = list(state.get("ran_ok") or [])
        if "status_since_work" in props:
            clean["status_since_work"] = bool(state.get("status_since_work"))
    return clean


def _annotation(prop: dict[str, Any], required: bool) -> Any:
    kind = prop.get("type") if isinstance(prop, dict) else None
    base: Any = {
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }.get(str(kind or ""), str)
    if required:
        return base
    return base | None


def tool_function(spec: dict[str, Any], call: Callable[[str, dict[str, Any]], str]) -> Callable[..., str]:
    """A real signature built from the visible schema. Strands reads inspect.signature."""
    props = visible_properties(spec)
    schema = spec.get("input_schema") if isinstance(spec.get("input_schema"), dict) else {}
    required = {name for name in (schema.get("required") or []) if name in props}
    parameters = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if name in required else None,
            annotation=_annotation(prop if isinstance(prop, dict) else {}, name in required),
        )
        for name, prop in props.items()
    ]
    name = str(spec.get("name") or "tool")

    def fn(**kwargs: Any) -> str:
        return call(name, kwargs)

    fn.__name__ = name
    fn.__doc__ = str(spec.get("description") or name)
    fn.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    fn.__annotations__ = {param.name: param.annotation for param in parameters}
    return fn


def _parse_body(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        return {"ok": False, "error": "empty gateway response"}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return parsed


def _model_text(body: Any) -> str:
    if not isinstance(body, dict):
        return str(body)
    if body.get("need_replan"):
        from cre_rentroll.eval_service import (
            ask_is_conceptual,
            ask_is_market_area,
            need_replan_hint,
        )
        from cre_rentroll import turn_ctx

        query = turn_ctx.eval_query() or ""
        if ask_is_conceptual(query) or ask_is_market_area(query):
            return need_replan_hint(query)
        return (
            "Need replan: call update_plan with the NEW remaining work. "
            "Completed/incomplete rows stay on the list. Do not call create_plan again. "
            "Status-only changes use update_plan_status."
        )
    text = body.get("text")
    if isinstance(text, str) and text.strip():
        return text
    return json.dumps(body)


def deal_call(name: str, model_args: dict[str, Any], specs: list[dict[str, Any]]) -> str:
    """Post one deal tools/call. Local score_gcr never comes through here."""
    from cre_rentroll import turn_ctx
    from cre_rentroll.capabilities_client import call_deal_tool

    spec = next((item for item in specs if item.get("name") == name), {"name": name})
    routed = turn_ctx.planless()
    if (
        routed
        and name != routed
        and name != "score_gcr"
        and not has_argument(spec, "checklist")
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
        turn_ctx.record(name, dict(model_args), err)
        return json.dumps(err)
    remote = stamp_arguments(
        spec,
        model_args,
        route="deal",
        username=turn_ctx.username() or "",
        as_of=turn_ctx.asked_as_of() or "",
        checklist=turn_ctx.checklist(),
        work=_work_state(specs, name),
    )
    try:
        body = _parse_body(call_deal_tool(name, remote))
    except Exception as exc:
        err = {"ok": False, "error": str(exc), "source": "capabilities-gateway"}
        turn_ctx.record(name, dict(model_args), err)
        return json.dumps(err)
    if isinstance(body, dict):
        rows = body.get("checklist")
        if isinstance(rows, list):
            turn_ctx.set_checklist([row for row in rows if isinstance(row, dict)])
        plan = body.get("plan")
        if isinstance(plan, str) and plan and has_argument(spec, "checklist"):
            turn_ctx.set_llm_plan(plan)
    turn_ctx.record(name, dict(model_args), body if isinstance(body, dict) else {"text": body})
    return _model_text(body)
