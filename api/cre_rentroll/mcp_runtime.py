"""In-process MCP for the Book agent — same interpreter, no stdio child.

stdio deal-vault / osm remain for standalone MCP hosts. The live Strands path
binds FastMCP servers here so ContextVars (username, checklist, traces) survive.
MCPClient uses a background thread; tool bodies re-enter the bound turn context.
"""

from __future__ import annotations

import functools
import inspect
import threading
from contextvars import Context, copy_context
from typing import Any, Callable

_lock = threading.Lock()
_turn_ctx: Context | None = None

VAULT_MCP = "deal-vault"
RENTROLL_MCP = "deal-rentroll"
OSM_MCP = "osm"

VAULT_TOOL_NAMES = frozenset(
    {
        "query_units",
        "compute_income",
        "compute_noi",
        "compute_cap_rate",
        "ask_vacancy_agent",
        "ask_prediction_agent",
    }
)
RENTROLL_TOOL_NAMES = frozenset(
    {
        "create_plan",
        "update_plan",
        "update_plan_status",
        "reflection",
        "score_gcr",
        "web_search",
        "publish_memo",
    }
)


def bind_turn_context() -> None:
    """Snapshot the calling thread's ContextVar state for MCP worker threads."""
    global _turn_ctx
    with _lock:
        _turn_ctx = copy_context()


def clear_turn_context() -> None:
    global _turn_ctx
    with _lock:
        _turn_ctx = None


def run_in_turn_context(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    with _lock:
        ctx = _turn_ctx
    if ctx is None:
        return fn(*args, **kwargs)
    return ctx.run(fn, *args, **kwargs)


def _unwrap(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Follow FastMCP FunctionTool.fn / functools wrappers to the Python body."""
    cur: Any = fn
    seen: set[int] = set()
    while id(cur) not in seen:
        seen.add(id(cur))
        nxt = getattr(cur, "fn", None) or getattr(cur, "func", None) or getattr(cur, "__wrapped__", None)
        if nxt is None or nxt is cur:
            break
        cur = nxt
    return cur


def _schema_origin(name: str, inner: Callable[..., Any]) -> Callable[..., Any]:
    """Prefer tools.py signatures/docs so Strands does not see *args/**kwargs."""
    try:
        from cre_rentroll.tools import TOOL_IMPLS

        origin = TOOL_IMPLS.get(name)
        if origin is not None:
            return origin
    except Exception:
        pass
    return inner


def _bound(fn: Callable[..., Any], name: str | None = None) -> Callable[..., Any]:
    inner = _unwrap(fn)
    tool_name = name or getattr(inner, "__name__", "tool")
    origin = _schema_origin(tool_name, inner)
    sig = inspect.signature(origin)

    @functools.wraps(origin)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        return run_in_turn_context(inner, *args, **kwargs)

    wrapped.__name__ = tool_name
    wrapped.__doc__ = origin.__doc__
    wrapped.__signature__ = sig  # type: ignore[attr-defined]
    wrapped.__annotations__ = dict(getattr(origin, "__annotations__", {}))
    return wrapped


def book_mcp_impls() -> dict[str, Callable[..., str]]:
    """FastMCP-registered implementations, ContextVar-bridged for in-process use."""
    try:
        from cre_rentroll import mcp_osm, mcp_rentroll, mcp_vault
    except ImportError:
        from cre_rentroll import tools as T

        return {name: _bound(fn, name) for name, fn in T.TOOL_IMPLS.items()}

    raw: dict[str, Callable[..., Any]] = {
        "query_units": mcp_vault.query_units,
        "compute_income": mcp_vault.compute_income,
        "compute_noi": mcp_vault.compute_noi,
        "compute_cap_rate": mcp_vault.compute_cap_rate,
        "ask_vacancy_agent": mcp_vault.ask_vacancy_agent,
        "ask_prediction_agent": mcp_vault.ask_prediction_agent,
        "create_plan": mcp_rentroll.create_plan,
        "update_plan": mcp_rentroll.update_plan,
        "update_plan_status": mcp_rentroll.update_plan_status,
        "reflection": mcp_rentroll.reflection,
        "score_gcr": mcp_rentroll.score_gcr,
        "web_search": mcp_rentroll.web_search,
        "publish_memo": mcp_rentroll.publish_memo,
        "osm_geocode": mcp_osm.osm_geocode,
    }
    return {name: _maybe_gateway(_bound(fn, name), name) for name, fn in raw.items()}


def _maybe_gateway(fn: Callable[..., Any], name: str) -> Callable[..., Any]:
    """Capability tools are only the capabilities-gateway client in the function body.

    The four checklist tools call the gateway from their own bodies. score_gcr
    stays in-process. AgentCore is not a fallback for the nine work tools.
    """
    from cre_rentroll.capabilities_client import CAPABILITY_TOOLS

    if name in CAPABILITY_TOOLS:
        return fn
    from cre_rentroll.agentcore_platform import GATEWAY_TOOL_NAMES, gateway_call

    if name not in GATEWAY_TOOL_NAMES:
        return fn
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        remote = gateway_call(name, dict(bound.arguments))
        if remote is not None:
            return remote
        from cre_rentroll.agentcore_platform import note_fastmcp_fallback

        note_fastmcp_fallback(name)
        return fn(*args, **kwargs)

    wrapped.__signature__ = sig  # type: ignore[attr-defined]
    return wrapped


def mcp_server_for(name: str) -> str | None:
    if name in VAULT_TOOL_NAMES:
        return VAULT_MCP
    if name == "osm_geocode":
        return OSM_MCP
    if name in RENTROLL_TOOL_NAMES:
        return RENTROLL_MCP
    return None
