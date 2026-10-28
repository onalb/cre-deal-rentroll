from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Callable, Iterator
import logging
import re
import sys
import time

MCP_TOOLS = frozenset(
    {
        "query_units",
        "compute_income",
        "compute_noi",
        "compute_cap_rate",
        "ask_vacancy_agent",
        "ask_vacancy",
        "ask_prediction_agent",
        "osm_geocode",
        "web_search",
        "create_plan",
        "update_plan",
        "update_plan_status",
        "reflection",
        "score_gcr",
    }
)
LOCAL_TOOLS = frozenset()
SKILLS = frozenset(
    {
        "DealRentroll",
        "RollAbstractor",
        "Locator",
        "Underwriter",
        "rent-roll-abstract",
        "citation-policy",
        "underwriting",
    }
)
_ALREADY_TYPED = r" \((?:skill|tool|MCP|LLM)\)"
_HAS_LLM = re.compile(r" \(LLM\)(?!\w)")

Sink = Callable[[dict[str, Any]], None]
_sink: ContextVar[Sink | None] = ContextVar("cre_run_sink", default=None)
_log = logging.getLogger("cre.run")
_CW_MAX = 1500


def _ensure_run_log() -> None:
    """App Runner / CloudWatch application logs are stdout. Uvicorn access is not enough."""
    _log.setLevel(logging.INFO)
    _log.propagate = False
    if _log.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(handler)


def _cloudwatch_line(step: str, message: str) -> str:
    one = " ".join((message or "").split())
    if len(one) > _CW_MAX:
        one = one[: _CW_MAX - 1] + "…"
    return f"RUN {step} {one}"


def bind(sink: Sink | None) -> Token:
    return _sink.set(sink)


def reset(token: Token) -> None:
    _sink.reset(token)


@contextmanager
def cloudwatch_only() -> Iterator[None]:
    """Keep stdout RUN lines; do not fan out to the UI SSE / compact Run Log sink."""
    token = bind(None)
    try:
        yield
    finally:
        reset(token)


def replay(events: list[dict[str, Any]] | None) -> None:
    """Fan in AgentCore logs to the App Runner SSE sink as they arrive (do not re-print RUN)."""
    fn = _sink.get()
    if fn is None:
        return
    for ev in events or []:
        if isinstance(ev, dict):
            fn(ev)


def name_kind(name: str) -> str | None:
    if name in MCP_TOOLS:
        return "MCP"
    if name in LOCAL_TOOLS:
        return "tool"
    if name in SKILLS:
        return "skill"
    return None


def labeled(name: str, kind: str | None = None) -> str:
    tag = kind or name_kind(name)
    return f"{name} ({tag})" if tag else name


def mark_llm(message: str) -> str:
    """Append (LLM) to operator text that came from a model."""
    if not message or _HAS_LLM.search(message):
        return message
    return f"{message} (LLM)"


def annotate_message(message: str) -> str:
    """Insert (skill)/(tool)/(MCP) after known names in operator-facing text."""
    if not message:
        return message
    out = message
    for name in sorted((n for n in (*MCP_TOOLS, *LOCAL_TOOLS, *SKILLS)), key=len, reverse=True):
        kind = name_kind(name)
        if not kind:
            continue
        out = re.sub(
            rf"(?<!\w){re.escape(name)}(?!\w)(?!{_ALREADY_TYPED})",
            f"{name} ({kind})",
            out,
        )
    return out


_REACT_STOP_SPLIT = re.compile(r"(?= stop=)")


def _with_react_task_suffix(message: str, tool_names: Any) -> str:
    from cre_rentroll.tools import react_turn_task_suffix

    names = [str(x) for x in (tool_names or []) if x]
    bit = react_turn_task_suffix(names)
    if not bit:
        return message
    extra = f" — {bit}"
    if extra in message or " — for " in message:
        return message
    parts = _REACT_STOP_SPLIT.split(message, maxsplit=1)
    if len(parts) == 2:
        return f"{parts[0]}{extra}{parts[1]}"
    return f"{message}{extra}"


def emit(step: str, message: str, *, llm: bool = False, **data: Any) -> None:
    """Push one operator-visible workflow line. No PII, no unit grids."""
    # Skills attach is an operator fact (prompt files), not a model/tool line.
    msg = message if step == "skills" else annotate_message(message)
    if step == "react":
        msg = _with_react_task_suffix(msg, data.get("tools"))
    if llm:
        msg = mark_llm(msg)
    _ensure_run_log()
    _log.info("%s", _cloudwatch_line(step, msg))
    fn = _sink.get()
    if fn is None:
        return
    payload = {
        "step": step,
        "message": msg,
        "data": {k: v for k, v in data.items() if v is not None},
        "ts": int(time.time() * 1000),
    }
    fn(payload)


def measure_tokens(text: str) -> int:
    return max(0, (len(text or "") + 3) // 4)


def emit_stop_and_summary(
    reason: str,
    *,
    traces: list[dict[str, Any]] | None = None,
    retry_n: int = 0,
    scores: Any = None,
    token_text: str = "",
    skills: list[str] | None = None,
    started: float | None = None,
) -> None:
    emit("stop", reason)
    calls = [str(t.get("name") or "") for t in (traces or []) if t.get("name")]
    mcp = [n for n in calls if n in MCP_TOOLS]
    uniq_mcp = list(dict.fromkeys(mcp))
    uniq_skills = list(dict.fromkeys(skills or []))
    elapsed = max(0.0, time.time() - started) if started else 0.0
    from cre_rentroll.turn_ctx import llm_token_summary

    tok_bit, tok_in, tok_out = llm_token_summary()
    conf = _confidence_line(scores, reason)
    mcp_bit = f"{len(mcp)} MCP" + (f" ({', '.join(labeled(n) for n in uniq_mcp)})" if uniq_mcp else "")
    skill_bit = f"{len(uniq_skills)} skill" + ("s" if len(uniq_skills) != 1 else "")
    if uniq_skills:
        skill_bit += f" ({', '.join(labeled(n) for n in uniq_skills)})"
    llm_bit, llm_n = llm_summary_bit()
    line = (
        f"{llm_bit} · {mcp_bit} · {skill_bit} · "
        f"{retry_n} retr{'ies' if retry_n != 1 else 'y'} · "
        f"{tok_bit} · {elapsed:.1f}s · confidence {conf}"
    )
    emit(
        "summary",
        line,
        llm_calls=llm_n,
        mcp=len(mcp),
        skills=len(uniq_skills),
        retries=retry_n,
        tokens_in=tok_in,
        tokens_out=tok_out,
        elapsed_s=round(elapsed, 2),
        confidence=conf,
    )


def llm_summary_bit() -> tuple[str, int]:
    from cre_rentroll.turn_ctx import llm_call_kinds

    kinds = llm_call_kinds()
    n = len(kinds)
    if n == 0:
        return "0 LLM calls", 0
    counts = Counter(kinds)
    order = ("scope", "agent", "judge")
    parts: list[str] = []
    seen: set[str] = set()
    for kind in order:
        if kind not in counts:
            continue
        seen.add(kind)
        if kind == "agent":
            parts.append(f"agent×{counts[kind]}")
        else:
            parts.append(kind)
    for kind, count in counts.items():
        if kind in seen:
            continue
        parts.append(f"{kind}×{count}" if count != 1 else kind)
    return f"{n} LLM ({', '.join(parts)})", n


def _confidence_line(scores: Any, reason: str) -> str:
    why = (reason or "").lower()
    if "out of scope" in why or "irrelevant" in why:
        return "n/a (out of scope)"
    if scores is None:
        if "scope" in why:
            return "n/a (out of scope)"
        return "n/a"
    judge = getattr(scores, "judge", None)
    r = getattr(scores, "R", None)
    g = getattr(scores, "G", None)
    c = getattr(scores, "request_C", None)
    vault = getattr(scores, "vault_calc_ok", None)
    nums = [n for n in (r, g, c) if isinstance(n, (int, float))]
    if not nums:
        if judge in {"empty", "not_run", None}:
            return "n/a (judge not run)"
        if judge == "error":
            return "n/a (judge failed)"
        return "n/a"
    avg = sum(nums) / len(nums)
    if vault is True and avg >= 4:
        band = "high"
    elif avg >= 3:
        band = "medium"
    else:
        band = "low"
    vault_s = "ok" if vault is True else "mismatch" if vault is False else "n/a"
    return f"{band} (R={r} G={g} C={c}, vault {vault_s})"


def _arg_true(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return value is True


def _query_units_log(shown: str, args: dict[str, Any], r: dict[str, Any]) -> str:
    """Operator line: full-roll unit_count is not the vacant list size."""
    vacant_only = _arg_true((args or {}).get("vacant_only"))
    occupancy = str((args or {}).get("occupancy") or "").strip()
    occ_key = occupancy.lower()
    filtered = occ_key not in {"", "all", "none"}
    bits = [shown]
    if vacant_only:
        bits.append("vacant_only")
    if filtered:
        bits.append(f"occupancy={occupancy}")
    prefix = " ".join(bits)
    total = r.get("unit_count")
    vacant = r.get("vacant_count")
    occupied = r.get("occupied_count")
    vacant_ask = vacant_only or occ_key == "vacant"
    occupied_ask = occ_key == "occupied" and not vacant_only
    mtm = f", MTM {r['mtm_count']}" if "mtm_count" in r else ""
    if vacant_ask and vacant is not None:
        of_total = f" (of {total})" if total is not None else ""
        return f"{prefix} → {vacant} vacant{of_total}{mtm}"
    if occupied_ask and occupied is not None:
        of_total = f" (of {total})" if total is not None else ""
        return f"{prefix} → {occupied} occupied{of_total}{mtm}"
    parts: list[str] = []
    if total is not None:
        parts.append(f"{total} units")
    if occupied is not None:
        parts.append(f"occupied {occupied}")
    if vacant is not None:
        parts.append(f"vacant {vacant}")
    if "mtm_count" in r:
        parts.append(f"MTM {r['mtm_count']}")
    return f"{prefix} → {', '.join(parts)}" if parts else prefix


# UI Run log: keep predictor prose intact. Only clip pathological blobs, and
# never in the middle of a sentence.
_PROSE_LOG_MAX = 50_000


def _prose_for_run_log(text: str, *, cap: int = _PROSE_LOG_MAX) -> str:
    body = str(text or "").strip()
    if not body or len(body) <= cap:
        return body
    cut = body[:cap]
    para = cut.rfind("\n\n")
    if para >= cap // 2:
        return cut[:para].rstrip() + "…"
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n", "\n"):
        idx = cut.rfind(sep)
        if idx >= cap // 2:
            end = idx + (1 if sep.startswith((".", "!", "?")) else 0)
            return cut[:end].rstrip() + "…"
    return cut.rstrip() + "…"


def tool_message(name: str, args: dict[str, Any], result: Any) -> str:
    r = result if isinstance(result, dict) else {}
    shown = labeled(name)
    if name == "query_units":
        return _query_units_log(shown, args or {}, r)
    if name == "compute_income":
        return (
            f"{shown} → monthly {r.get('in_place_monthly')}, "
            f"occupied {r.get('occupied_count')}, vacant {r.get('vacant_count')}, "
            f"MTM {r.get('mtm_count')}"
        )
    if name == "compute_noi":
        if r.get("noi_incomplete"):
            return f"{shown} → NOI incomplete (no opex)"
        return f"{shown} → NOI {r.get('noi')}"
    if name == "compute_cap_rate":
        return f"{shown} → {r.get('implied_cap')} on ask {r.get('ask_price')}"
    if name == "ask_vacancy_agent":
        plans = r.get("plans") or []
        return f"{shown} → vacancy agent scored {len(plans)} floor plans"
    if name == "ask_vacancy":
        if r.get("ok") is False:
            err = _prose_for_run_log(r.get("error") or "failed")
            return f"{shown} failed: {err}"
        answer = _prose_for_run_log(r.get("answer") or "")
        return f"{shown} → {answer}" if answer else f"{shown} → vacancy answer"
    if name == "ask_prediction_agent":
        if r.get("ok") is False:
            err = _prose_for_run_log(r.get("error") or "failed")
            return f"{shown} failed: {err}"
        answer = _prose_for_run_log(r.get("answer") or "")
        return f"{shown} → {answer}" if answer else f"{shown} → prediction agent"
    if name == "osm_geocode":
        if r.get("ok"):
            return f"{shown} → {r.get('display_name')}"
        return f"{shown} failed: {r.get('error')}"
    if name == "web_search":
        if r.get("ok") is False:
            return f"{shown} failed: {r.get('error')}"
        n = len(r.get("results") or [])
        q = re.sub(r"\s+", " ", str((args or {}).get("query") or "")).strip()
        return f"{shown} → {n} hits ({q})" if q else f"{shown} → {n} hits"
    if name in {"create_plan", "update_plan", "update_plan_status"}:
        plan = r.get("log") or r.get("hint") or r.get("plan") or (args or {}).get("tasks") or ""
        if isinstance(plan, (list, tuple)):
            plan = "\n".join(str(x) for x in plan)
        body = str(plan).strip()
        return f"{shown} — {body}" if body else shown
    if name == "reflection":
        analysis = (args or {}).get("analysis") or r.get("analysis") or ""
        body_txt = str(analysis).strip()
        flag = " need_replan" if ((args or {}).get("need_replan") or r.get("need_replan")) else ""
        shown_r = f"{shown}{flag}"
        return f"{shown_r} — {body_txt}" if body_txt else shown_r
    if name == "score_gcr":
        from cre_rentroll.eval_service import append_fail_why, format_fail_why, vault_calc_log_label
        from cre_rentroll.schemas import EvalScores

        vault_label = r.get("vault_calc") or vault_calc_log_label(
            r.get("vault_calc_ok"),
            r.get("vault_invented") or [],
            draft=(args or {}).get("draft"),
        )
        g = "N/A" if r.get("G") is None else r.get("G")
        c = "N/A" if r.get("request_C") is None else r.get("request_C")
        rv = "N/A" if r.get("R") is None else r.get("R")
        line = (
            f"{shown} → G={g} request-C={c} R={rv} "
            f"vault_calc={vault_label} "
            f"judge={r.get('judge')} → {r.get('decision')}"
        )

        comment = r.get("reason") or r.get("notes") or r.get("justification")
        why = format_fail_why(
            EvalScores(
                R=r.get("R"),
                G=r.get("G"),
                request_C=r.get("request_C"),
                vault_calc_ok=r.get("vault_calc_ok"),
                vault_calc=vault_label,
                judge=r.get("judge"),
                reason=comment if isinstance(comment, str) else None,
            ),
            r.get("decision"),
        )
        return append_fail_why(line, why)
    return shown
