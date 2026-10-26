"""Follow-up menu state for ask_prediction_agent. Not an MCP client.

The gateway owns the vacancy MCP session. This module only remembers whether
the last gateway reply asked a follow-up, so the next user turn can keep the
tool on the menu, and recognizes a clarifying answer already in this turn.
"""

from __future__ import annotations

import re
from typing import Any

_OCCUPANCY_ONLY_RE = re.compile(
    r"^what occupancy do you predict(?:\s+for\s+.+?)?(?:\s+as of\s+.+?)?\??$",
    re.I,
)
_CLARIFY_RE = re.compile(
    r"(?is)("
    r"not trained|no (?:saved )?fit|not saved|"
    r"which (?:floor )?plan|which group|which variant|"
    r"which .{0,60}renovat|"
    r"ambiguous_plan|"
    r"which properties (?:are|have been) trained|"
    r"have not been trained"
    r")"
)
_NEW_PREDICT_RE = re.compile(
    r"(?is)\b("
    r"what occupancy do you predict|"
    r"over the next\s+\d+\s+months|"
    r"predicted occupancy|"
    r"forecast occupancy"
    r")\b"
)

_state: dict[str, dict[str, Any]] = {}


def reset_predict_state() -> None:
    _state.clear()


def note_awaiting_followup(username: str, awaiting: bool) -> None:
    row = _state.setdefault(username or "", {})
    row["awaiting_followup"] = bool(awaiting)


def session_awaiting_followup(username: str) -> bool:
    row = _state.get(username or "")
    return bool(row and row.get("awaiting_followup"))


def looks_like_clarification(text: str) -> bool:
    """True when a prediction reply asked a follow-up or said the building has no fit."""
    return bool(_CLARIFY_RE.search((text or "").strip()))


def is_followup_reply(message: str) -> bool:
    """True for a short plan/variant reply, not a new occupancy/forecast ask."""
    asked = (message or "").strip()
    if not asked:
        return False
    if _OCCUPANCY_ONLY_RE.match(asked):
        return False
    if _NEW_PREDICT_RE.search(asked) and len(asked) > 48:
        return False
    return True


def this_turn_clarify_result() -> dict[str, Any] | None:
    """First ask_prediction_agent clarification already recorded this user turn."""
    try:
        from cre_rentroll import turn_ctx

        traces = turn_ctx.traces()
    except Exception:
        return None
    for row in traces or []:
        if str(row.get("name") or "") != "ask_prediction_agent":
            continue
        result = row.get("result")
        if not isinstance(result, dict):
            continue
        answer = str(result.get("answer") or "").strip()
        if answer and looks_like_clarification(answer):
            return result
    return None
