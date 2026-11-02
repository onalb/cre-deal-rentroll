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
from gold_eval.score import place_hit, score_accuracy, score_tool_calls

__all__ = [
    "MAX_RUNS",
    "case_prompts",
    "case_query",
    "eval_username",
    "expected_tools",
    "gold_cases_path",
    "iter_gold",
    "load_cases",
    "place_hit",
    "resolve_xlsx",
    "score_accuracy",
    "score_stats",
    "score_tool_calls",
    "summarize",
    "tools_match",
    "xlsx_candidates",
]
