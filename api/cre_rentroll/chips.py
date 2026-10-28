"""Whether ask_prediction_agent is on the tool menu.

Demo chips only paste a sample question into the composer. They are not an
input to the model, the tool menu, or the system prompt.
"""

from __future__ import annotations

import re

PREDICT_TOOL = "ask_prediction_agent"

_FORWARD_RE = re.compile(
    r"\b("
    r"forecast|predict(?:ed|ion)?|outlook|"
    r"expir(?:e|es|ing|ation|ations)|"
    r"rollover|roll[\s-]*over|"
    r"loss[\s-]*to[\s-]*lease|"
    r"income at risk|"
    r"next\s+\d+\s+months?|"
    r"over the next|"
    r"12[\s-]*month"
    r")\b",
    re.I,
)


def is_forward_looking(message: str) -> bool:
    return bool(_FORWARD_RE.search(message or ""))


def allow_predict(message: str = "", username: str = "") -> bool:
    """Predict tool is in inventory when the question is forward-looking.
    After vacancy MCP asked a clarifying question, keep the tool on for a
    short plan/variant reply (same username session)."""
    if username:
        from cre_rentroll.predict_followup import (
            is_followup_reply,
            session_awaiting_followup,
        )

        if session_awaiting_followup(username) and is_followup_reply(message):
            return True
    return is_forward_looking(message)


def filter_book_tools(
    tool_names: tuple[str, ...],
    message: str = "",
    username: str = "",
) -> tuple[str, ...]:
    if allow_predict(message, username=username):
        return tool_names
    return tuple(n for n in tool_names if n != PREDICT_TOOL)
