"""Gold-set test runner: cases, scoring helpers, and the eval HTTP routes."""

from gold_eval.runner import (
    MAX_RUNS,
    case_prompts,
    case_query,
    eval_username,
    expected_tools,
    gold_cases_path,
    iter_gold,
    load_cases,
    resolve_xlsx,
    score_stats,
    summarize,
    tools_match,
    xlsx_candidates,
)

__all__ = [
    "MAX_RUNS",
    "case_prompts",
    "case_query",
    "eval_username",
    "expected_tools",
    "gold_cases_path",
    "iter_gold",
    "load_cases",
    "resolve_xlsx",
    "score_stats",
    "summarize",
    "tools_match",
    "xlsx_candidates",
]
