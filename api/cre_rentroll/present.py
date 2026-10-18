from __future__ import annotations

import re

_RETRY = re.compile(r"\n*\[eval retry:.*?\]\s*$", re.I | re.S)
_EVAL_BANNER = re.compile(r"\n*—\s*Warning:\s*live eval[\s\S]*$", re.I)
_OPERATOR_LEAK = re.compile(
    r"(?is)^\s*(Need replan:|Plan replaced|Plan updated|Plan unchanged|"
    r"Checklist already exists|Reflection stored|No checklist yet|"
    r"Search already failed)"
)


def user_visible(raw: str) -> str:
    s = _RETRY.sub("", raw or "")
    return s.strip() or (raw or "").strip()


def is_operator_leak(draft: str) -> bool:
    text = (draft or "").strip()
    if not text:
        return False
    if _OPERATOR_LEAK.match(text):
        return True
    low = text.lower()
    return "call update_plan" in low and "new remaining work" in low


def answer_prose(draft: str, as_of: str | None = None, question: str | None = None) -> str:
    """Chat text is the agent draft. Strip leftover eval banners only."""
    _ = as_of, question
    return _EVAL_BANNER.sub("", draft or "").strip()


def scope_fail_chat(detail: str) -> str:
    d = (detail or "").lower()
    if "429" in d or "rate" in d:
        return "The model is busy right now. Wait a minute and send again."
    if "key" in d or "unauthorized" in d or "missing" in d:
        return "The app cannot reach the model right now. Check the API key and try again."
    if "timeout" in d or "network" in d:
        return "That request timed out. Wait a moment and send again."
    return "I could not start this question. Wait a moment and send again."
