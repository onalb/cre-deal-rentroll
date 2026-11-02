"""Standalone gold evaluator. Uploads one rent-roll Excel, then runs gold.json."""

from __future__ import annotations

import os
import time
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Iterator

from gold_eval.score import score_accuracy, score_tool_calls, slim_work_trace, work_tools

DISPLAY_NAME = "La Cordoba Rent Roll 04-20-21 (2) 164.xlsx"
MAX_RUNS = 5

_PKG = Path(__file__).resolve().parent
_REPO = _PKG.parent

_WORK_TOOLS = work_tools()


def gold_cases_path() -> Path:
    env = (os.environ.get("GOLD_EVAL_CASES") or "").strip()
    if env:
        return Path(env)
    return _PKG / "gold.json"


def xlsx_candidates() -> list[Path]:
    found: list[Path] = []
    env = (os.environ.get("GOLD_EVAL_XLSX") or "").strip()
    if env:
        found.append(Path(env))
    found.append(Path("/app/gold_eval/gold/lacordoba.xlsx"))
    found.append(_REPO / "gold_eval" / "gold" / "lacordoba.xlsx")
    found.append(Path.home() / "Desktop" / "ML Interview" / DISPLAY_NAME)
    return found


def resolve_xlsx() -> Path:
    for path in xlsx_candidates():
        if path.is_file():
            return path
    raise FileNotFoundError(DISPLAY_NAME)


def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    import json

    raw = (path or gold_cases_path()).read_text(encoding="utf-8")
    cases = json.loads(raw)["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("gold.json has no cases")
    return cases


def case_query(case: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    """The user prompt actually sent for one case."""
    _ = cases
    return str(case["query"])


def case_prompts(path: Path | None = None) -> list[dict[str, str]]:
    """Case ids and the user prompt actually sent to the agent."""
    cases = load_cases(path)
    prompts: list[dict[str, str]] = []
    for case in cases:
        prompts.append({"id": str(case["id"]), "query": case_query(case, cases)})
    return prompts


def expected_tools(case: dict[str, Any], cases: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    _ = cases
    if case.get("scope") == "IRRELEVANT":
        return set(), set(_WORK_TOOLS)
    best = case.get("best_calls") or {}
    required = {str(name) for name in best} or {str(name) for name in (case.get("required_tools") or [])}
    forbidden = {str(name) for name in (case.get("forbidden_tools") or [])}
    return required, forbidden


def tools_match(case: dict[str, Any], cases: list[dict[str, Any]], called: list[str]) -> dict[str, Any]:
    _ = cases
    return score_tool_calls(case, called)


def score_stats(values: list[Any]) -> dict[str, Any]:
    nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return {"mean": None, "stdev": None, "n": 0}
    return {
        "mean": mean(nums),
        "stdev": stdev(nums) if len(nums) > 1 else 0.0,
        "n": len(nums),
    }


def summarize(order: list[str], rows_by_case: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    summary = []
    for case_id in order:
        rows = rows_by_case.get(case_id) or []
        summary.append(
            {
                "id": case_id,
                "runs": len(rows),
                "G": score_stats([row.get("G") for row in rows]),
                "C": score_stats([row.get("C") for row in rows]),
                "R": score_stats([row.get("R") for row in rows]),
                "accuracy_passed": sum(1 for row in rows if row.get("accuracy") is True),
                "tool_calls_passed": sum(1 for row in rows if row.get("tools_ok")),
            }
        )
    return summary


def eval_username(username: str) -> str:
    return f"{username}::gold"


def _run_row(
    case: dict[str, Any],
    cases: list[dict[str, Any]],
    roll: Any,
    *,
    answer: str,
    called: list[str],
    scores: Any,
    kind: str,
    stop_reason: str,
    error: str,
    elapsed: float,
) -> dict[str, Any]:
    from cre_rentroll import turn_ctx

    trace = slim_work_trace(turn_ctx.traces())
    names = [item["name"] for item in trace] or list(called)
    calls = score_tool_calls(case, names)
    accuracy = score_accuracy(case, answer, roll)
    _bit, tok_in, tok_out = turn_ctx.llm_token_summary()
    return {
        "ok": not error,
        "error": error,
        "G": None if scores is None else scores.G,
        "C": None if scores is None else scores.request_C,
        "R": None if scores is None else scores.R,
        "judge": None if scores is None else scores.judge,
        "accuracy": accuracy["pass"],
        "facts": accuracy["facts"],
        "gold_answer": accuracy.get("gold_answer") or "",
        "tools_ok": calls["ok"],
        "tool_calls": calls,
        "tools_called": names,
        "prompt": case_query(case, cases),
        "answer": answer,
        "plan": turn_ctx.llm_plan() or "",
        "trace": trace,
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "elapsed_s": round(elapsed, 1),
        "kind": kind,
        "stop_reason": stop_reason,
        "preview": (answer or "")[:240],
    }


def iter_gold(username: str, runs: int) -> Iterator[dict[str, Any]]:
    """Upload the rent roll, then run every gold case `runs` times."""
    from cre_rentroll import harness, vault

    if runs < 1 or runs > MAX_RUNS:
        raise ValueError(f"runs must be from 1 to {MAX_RUNS}")
    cases = load_cases()
    path = resolve_xlsx()
    raw = path.read_bytes()
    user = eval_username(username)
    order = [str(case["id"]) for case in cases]
    rows_by_case: dict[str, list[dict[str, Any]]] = {case_id: [] for case_id in order}

    yield {
        "event": "status",
        "phase": "upload",
        "file": DISPLAY_NAME,
        "message": f"Uploading {DISPLAY_NAME}",
    }
    vault.save_session(user, [], eval_retry_used=False)
    ingested = harness.ingest_attachment(user, DISPLAY_NAME, raw)
    roll = vault.load_roll(user)
    base = list(vault.load_session(user).get("messages") or [])
    info = ingested.ingest or {}
    yield {
        "event": "status",
        "phase": "uploaded",
        "file": DISPLAY_NAME,
        "message": (
            f"Uploaded {info.get('property_name') or DISPLAY_NAME}, "
            f"{info.get('unit_count')} units"
        ),
        "ingest": info,
    }

    for case in cases:
        case_id = str(case["id"])
        query = case_query(case, cases)
        for run in range(1, runs + 1):
            yield {
                "event": "status",
                "phase": "running",
                "case": case_id,
                "run": run,
                "runs": runs,
                "message": f"{case_id} · run {run} of {runs}",
            }
            vault.save_session(user, base, eval_retry_used=False)
            started = time.perf_counter()
            try:
                outcome = harness.run_task(user, query)
            except Exception as exc:
                row = _run_row(
                    case,
                    cases,
                    roll,
                    answer="",
                    called=[],
                    scores=None,
                    kind="error",
                    stop_reason="error",
                    error=f"{type(exc).__name__}: {exc}",
                    elapsed=time.perf_counter() - started,
                )
            else:
                row = _run_row(
                    case,
                    cases,
                    roll,
                    answer=outcome.text or "",
                    called=list(outcome.tools_called or []),
                    scores=outcome.eval,
                    kind=outcome.kind,
                    stop_reason=outcome.stop_reason or "",
                    error="",
                    elapsed=time.perf_counter() - started,
                )
            rows_by_case[case_id].append(row)
            yield {
                "event": "run",
                "phase": "done",
                "case": case_id,
                "run": run,
                "runs": runs,
                "message": f"{case_id} · run {run} of {runs} finished",
                **row,
                "summary": summarize(order, rows_by_case),
            }
            if row.get("stop_reason") == "openai_rate_limited":
                yield {
                    "event": "error",
                    "message": "The model is busy right now. Wait a minute and start again.",
                    "summary": summarize(order, rows_by_case),
                }
                return

    yield {
        "event": "done",
        "phase": "done",
        "file": DISPLAY_NAME,
        "message": "Finished",
        "summary": summarize(order, rows_by_case),
    }
