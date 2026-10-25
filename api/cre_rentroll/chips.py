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

PREDICT_CHIP_HINT = (
    "The user selected the Predict occupancy (full) chip. If THIS last message is "
    "forward-looking (forecast occupancy, expirations, MTM waterfall, rollover, "
    "loss-to-lease, predicted income), you SHOULD call ask_prediction_agent "
    "(MCP /mcp ask_vacancy on RR_PREDICT_URL, default http://127.0.0.1:8000). "
    "Pass the vault property name in the question. Rentroll does not upload "
    "or train that agent — training is only via the vacancy-agent UI. Cite "
    "its reply even if the building is not trained or it asks which floor plan. "
    "Do not force the call for a current-book rent lookup. Do not use "
    "query_units or ask_vacancy_agent for that forecast. Skip prediction for "
    "geocode, weather, or today's occupancy/income."
)

PREDICT_ONLY_CHIP_HINT = (
    "The user selected the occupancy-only Predict occupancy chip. If THIS last "
    "message is forward occupancy, you SHOULD call ask_prediction_agent "
    "(MCP /mcp ask_vacancy on RR_PREDICT_URL, default http://127.0.0.1:8000). "
    "Answer occupancy only — do not expand into expirations, MTM waterfall, "
    "loss-to-lease, or predicted income unless occupancy itself. Ask predicted "
    "occupancy for the vault property_name as of as_of. Rentroll does "
    "not upload or train that agent — training is only via the vacancy-agent UI. "
    "Cite its reply even if the building is not trained or it asks which floor plan. "
    "Do not force the call for a current-book rent lookup. Do not use "
    "query_units or ask_vacancy_agent for that forecast. Skip prediction for "
    "geocode, weather, or today's occupancy."
)


def is_forward_looking(message: str) -> bool:
    return bool(_FORWARD_RE.search(message or ""))


def allow_predict(chip_id: str = "", message: str = "") -> bool:
    """Predict tool is in inventory when the Predict chip is on, or no chip
    and the question is clearly forward-looking. Other chips hide it."""
    cid = (chip_id or "").strip()
    if not cid:
        return is_forward_looking(message)
    return bool(_CHIP_ALLOW_PREDICT.get(cid, False))


FORWARD_HINT = (
    "This question looks forward-looking. You SHOULD call ask_prediction_agent "
    "(HTTP chat to :8000) for forecast occupancy, expirations, MTM, "
    "rollover, loss-to-lease, or predicted income. Do not upload or train. "
    "Skip it for current-book rent, geocode, or weather."
)


def chip_prompt(chip_id: str = "", message: str = "") -> str:
    cid = (chip_id or "").strip()
    if cid == PREDICT_CHIP:
        return PREDICT_CHIP_HINT
    if cid == PREDICT_ONLY_CHIP:
        return PREDICT_ONLY_CHIP_HINT
    if not cid and is_forward_looking(message):
        return FORWARD_HINT
    return ""


def filter_book_tools(tool_names: tuple[str, ...], chip_id: str = "", message: str = "") -> tuple[str, ...]:
    if allow_predict(chip_id, message):
        return tool_names
    return tuple(n for n in tool_names if n != PREDICT_TOOL)
