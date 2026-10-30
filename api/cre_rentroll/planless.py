"""Deterministic single-tool routing: known one-tool asks skip create_plan.

The harness (this module), not the model, decides planlessness — that turn's
tool menu simply has no create_plan, exactly like chips.py decides
ask_prediction_agent. Stay planned only when a second *tool family* is
present, not because the ask lists several this-roll figures with "and":
- this-roll figure lists (units, occupancy, vacant, MTM, in-place income) → query_units
- income-only this-roll → compute_income
- map + occupancy / cap + NOI / locate corrections → plan
- forecast / predicted occupancy or vacancy → ask_prediction_agent
- plan/variant reply after the predictor asked which group → ask_prediction_agent
- vault-empty this-roll occupancy/income asks → need-file (no tools, no plan)
"""

from __future__ import annotations

import re

# One work tool per category. The turn is planless only when exactly one
# category fires and no second-family marker appears.
_OCCUPANCY_RE = re.compile(
    r"\b(occupanc\w*|vacan\w+|how many units?|unit count|"
    r"month[\s-]*to[\s-]*month|mtm|avg\.?\s*sqft|average\s+sqft|square\s+feet|"
    r"typical\s+unit\s+size)\b",
    re.I,
)
_MAP_ONLY_RE = re.compile(
    r"\b(where is (this|the)|locate|map\b|pin|show me (it|the map))\b",
    re.I,
)
_DEFINITION_RE = re.compile(
    r"\b(what('| i)?s|define|definition of|meaning of|how do you calculat\w+)\b",
    re.I,
)
_AREA_FACTS_RE = re.compile(
    r"\b(average (property )?value|comps?|comparable\w*|demographics?)\b",
    re.I,
)
_THIS_ROLL_INCOME_RE = re.compile(
    r"\b(in[\s-]*place|income)\b",
    re.I,
)
_FORWARD_RE = re.compile(
    r"\b(forecast|predict\w*|outlook|waterfall|"
    r"next\s+\d+\s+months?|over the next|12[\s-]*month)\b",
    re.I,
)

# A second *tool family* — not a second this-roll figure — keeps the plan.
_MUST_PLAN_RE = re.compile(
    r"\b("
    r"cap[\s-]*rate|noi|net\s+operating\s+income|"
    r"buy|worth|acquire|underwrit\w*|"
    # Locate corrections are web_search THEN osm_geocode — two tools.
    r"wrong (country|place|town|city)|actually|search the map again|"
    r"correct(?:ed|ion)?"
    r")\b",
    re.I,
)


def single_tool_intent(
    message: str,
    *,
    has_vault: bool,
    predict_followup: bool = False,
) -> str | None:
    """One of query_units / compute_income / osm_geocode / web_search /
    ask_prediction_agent, or None.

    Categories, not conjunctions, decide the plan. Several this-roll figures
    joined by "and" are still one tool. A prediction is one tool
    (ask_prediction_agent) even when it also says occupancy or vacancy.
    A short plan-name reply after the predictor asked which group is the
    same tool. Map + occupancy or income + NOI stay planned. Occupancy with
    no vault returns None so it lands on the need-file path, never web_search.
    """
    text = (message or "").strip()
    must_plan = bool(_MUST_PLAN_RE.search(text)) if text else False
    forward = bool(_FORWARD_RE.search(text)) if text else False
    if predict_followup:
        forward = True
    occ = bool(_OCCUPANCY_RE.search(text)) if text else False
    income = bool(_THIS_ROLL_INCOME_RE.search(text)) if text else False
    this_roll = occ or income
    map_hit = bool(_MAP_ONLY_RE.search(text)) if text else False
    definition = bool(_DEFINITION_RE.search(text)) if text else False
    area = bool(_AREA_FACTS_RE.search(text)) if text else False
    # "What's the occupancy" matches definition — ignore that when this-roll.
    search = (definition or area) and not this_roll and not map_hit and not forward
    cats: list[str] = []
    if forward:
        cats.append("predict")
    elif this_roll:
        cats.append("this_roll")
    if map_hit:
        cats.append("map")
    if search:
        cats.append("search")
    result: str | None = None
    if text and not must_plan and len(cats) == 1:
        if cats[0] == "predict":
            result = "ask_prediction_agent"
        elif cats[0] == "this_roll":
            if has_vault and occ:
                result = "query_units"
            elif has_vault:
                result = "compute_income"
        elif cats[0] == "map":
            result = "osm_geocode"
        else:
            result = "web_search"
    return result


def this_roll_needs_file(message: str, *, has_vault: bool) -> bool:
    """True when THIS-roll figures are asked and there is no ingested roll.

    Map-only and area/market facts can proceed without a vault. Occupancy /
    units / in-place income cannot — skip ReAct so the model cannot burn a
    create_plan + query_units round-trip just to hit need_file.
    """
    if has_vault:
        return False
    text = (message or "").strip()
    if not text:
        return False
    # Predictions go through ask_prediction_agent; they do not need the vault
    # on this hop (the vacancy agent may ask for a floor plan).
    if _FORWARD_RE.search(text):
        return False
    occ = bool(_OCCUPANCY_RE.search(text))
    income = bool(_THIS_ROLL_INCOME_RE.search(text))
    if not (occ or income):
        return False
    return True