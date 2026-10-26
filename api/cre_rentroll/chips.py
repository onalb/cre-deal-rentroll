"""UI chips steer inventory and the system prompt. They do not force a tool call."""

from __future__ import annotations

import re

PREDICT_TOOL = "ask_prediction_agent"
PREDICT_CHIP = "predict"
PREDICT_ONLY_CHIP = "predict_occupancy_only"
PREDICT_CHIPS = frozenset({PREDICT_CHIP, PREDICT_ONLY_CHIP})

# Current-book, geocode, weather, and other demo chips hide prediction.
_CHIP_ALLOW_PREDICT = {
    "abstract": False,
    "predict": True,
    "predict_occupancy_only": True,
    "multi-1": False,
    "multi-2": False,
    "multi-3": False,
    "locate": False,
    "create-memo": False,
    "underwrite": False,
    "buy": False,
    "search-down": False,
    "weather": False,
}

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

_QUOTE_MCP = (
    "If ask_prediction_agent says the building is not trained (lists trained "
    "properties) or asks which floor plan / group / renovated variant (a bullet "
    "list of plans, no probabilities), that reply IS the assistant message — "
    "quote it and STOP this turn. Do not call ask_prediction_agent again this "
    "turn with a guessed plan or property. Do not invent occupancy from the "
    "vault roll or query_units. Never pick a property or floor plan the user "
    "did not write in THIS user message. If they did not name a property, do "
    "not substitute a vault name or a name from the trained list — forward "
    "their question. If the NEXT user message is a plan name or renovated "
    "yes/no after that turn, call ask_prediction_agent again with that text "
    "(same MCP session_id)."
)

PREDICT_CHIP_HINT = (
    "The user selected the Predict occupancy (full) chip. If THIS last message is "
    "forward-looking (forecast occupancy, expirations, MTM waterfall, rollover, "
    "loss-to-lease, predicted income), you SHOULD call ask_prediction_agent "
    "(capabilities gateway POST /mcp/deal tool ask_prediction_agent). "
    "Pass THIS user question (plus do not parse/train). Do not invent a building. "
    "Rentroll does not upload or train that agent — training is only via the "
    "vacancy-agent UI. "
    f"{_QUOTE_MCP} "
    "Do not force the call for a current-book rent lookup. Do not use "
    "query_units or ask_vacancy_agent for that forecast. Skip prediction for "
    "geocode, weather, or today's occupancy/income."
)

PREDICT_ONLY_CHIP_HINT = (
    "The user selected the occupancy-only Predict occupancy chip. If THIS last "
    "message is forward occupancy, you SHOULD call ask_prediction_agent "
    "(capabilities gateway POST /mcp/deal tool ask_prediction_agent). "
    "Answer occupancy only — do not expand into expirations, MTM waterfall, "
    "loss-to-lease, or predicted income unless occupancy itself. Forward THIS "
    "user question; do not add a property or as-of the user did not write. "
    "Rentroll does not upload or train that agent — training is only via the "
    "vacancy-agent UI. "
    f"{_QUOTE_MCP} "
    "Do not force the call for a current-book rent lookup. Do not use "
    "query_units or ask_vacancy_agent for that forecast. Skip prediction for "
    "geocode, weather, or today's occupancy."
)


def is_forward_looking(message: str) -> bool:
    return bool(_FORWARD_RE.search(message or ""))


FORWARD_HINT = (
    "This question looks forward-looking. You SHOULD call ask_prediction_agent "
    "(capabilities gateway POST /mcp/deal tool ask_prediction_agent) for forecast occupancy, expirations, MTM, "
    "rollover, loss-to-lease, or predicted income. Do not upload or train. "
    f"{_QUOTE_MCP} "
    "Skip it for current-book rent, geocode, or weather."
)

FOLLOWUP_HINT = (
    "The last prediction turn asked a follow-up (which floor plan / group, or "
    "the building is not trained). Call ask_prediction_agent with THIS user "
    "message only — do not pick a plan or property they did not write. Quote "
    "the agent's reply. Do not compute occupancy from the vault roll."
)


def allow_predict(chip_id: str = "", message: str = "", username: str = "") -> bool:
    """Predict tool is in inventory when the Predict chip is on, or no chip
    and the question is clearly forward-looking. Other chips hide it.
    After vacancy MCP asked a clarifying question, keep the tool on for a
    short plan/variant reply (same username session)."""
    cid = (chip_id or "").strip()
    if cid:
        return bool(_CHIP_ALLOW_PREDICT.get(cid, False))
    if username:
        from cre_rentroll.predict_followup import (
            is_followup_reply,
            session_awaiting_followup,
        )

        if session_awaiting_followup(username) and is_followup_reply(message):
            return True
    return is_forward_looking(message)


def chip_prompt(chip_id: str = "", message: str = "", username: str = "") -> str:
    cid = (chip_id or "").strip()
    text = ""
    if cid == PREDICT_CHIP:
        text = PREDICT_CHIP_HINT
    elif cid == PREDICT_ONLY_CHIP:
        text = PREDICT_ONLY_CHIP_HINT
    elif username:
        from cre_rentroll.predict_followup import (
            is_followup_reply,
            session_awaiting_followup,
        )

        if session_awaiting_followup(username) and is_followup_reply(message):
            text = FOLLOWUP_HINT
    if not text and not cid and is_forward_looking(message):
        text = FORWARD_HINT
    return _gateway_chip_text(text)


def _gateway_chip_text(text: str) -> str:
    """When the gateway URL is set, name that host. Tool names stay put."""
    if not text:
        return text
    from cre_rentroll.capabilities_client import gateway_base

    base = gateway_base()
    if not base:
        return text
    return text.replace(
        "capabilities gateway POST /mcp/deal",
        f"capabilities gateway {base}/mcp/deal",
    )


def filter_book_tools(
    tool_names: tuple[str, ...],
    chip_id: str = "",
    message: str = "",
    username: str = "",
) -> tuple[str, ...]:
    if allow_predict(chip_id, message, username=username):
        names = tool_names
    else:
        names = tuple(n for n in tool_names if n != PREDICT_TOOL)
    return names
