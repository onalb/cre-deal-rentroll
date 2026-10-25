from __future__ import annotations

import json
import re
from statistics import mean, stdev
from typing import Any

from cre_rentroll.schemas import CanonicalRentRoll, EvalScores

_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_NUM_TOKEN = re.compile(r"-?\d[\d,]*(?:\.\d+)?%?")
_STRUCTURAL = {0.0, 1.0, 12.0, 100.0}
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)"
)
_DATE_RES = (
    re.compile(rf"\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s*\d{{4}})?\b", re.I),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:,?\s*\d{{4}})?\b", re.I),
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
)


def _numbers(text: str) -> set[str]:
    out = set()
    for m in _NUM.findall(text or ""):
        try:
            out.add(f"{float(m.replace(',', '')):.4f}")
        except ValueError:
            continue
    return out


def _same_magnitude(x: float, y: float) -> bool:
    """Near-equal after 2-decimal / whole-percent rounding — not 8 vs 29."""
    if abs(x - y) / max(abs(x), abs(y), 1e-9) < 1e-3:
        return True
    if abs(round(x, 2) - round(y, 2)) < 1e-9:
        return True
    # Whole displayed percent (29.08 vs 29) once both are on the percent scale.
    if min(abs(x), abs(y)) >= 1.0 and abs(x - y) < 0.51:
        return True
    if max(abs(x), abs(y)) <= 1.0000001 and abs(x * 100.0 - y * 100.0) < 0.51:
        return True
    return False


def _num_close(v: float, a: float) -> bool:
    """Same figure after rounding, or rate-style 0.2908 vs 29.08% (not 8% vs 29%)."""
    if _same_magnitude(v, a):
        return True
    lo, hi = (v, a) if abs(v) <= abs(a) else (a, v)
    if 0 < lo <= 1.0 and hi > 1.0:
        return _same_magnitude(lo * 100.0, hi)
    return False


def _with_percent_twins(allowed: set[str]) -> set[str]:
    extra: set[str] = set()
    for a in allowed:
        try:
            v = float(a)
        except ValueError:
            continue
        if 0 < v <= 1.0000001:
            extra |= _numbers(str(v * 100.0))
    return allowed | extra


def this_turn_calc_spans(traces: list[dict[str, Any]]) -> list[str]:
    spans: list[str] = []
    for t in traces:
        if t.get("name") not in {"compute_income", "compute_noi", "compute_cap_rate"}:
            continue
        spans.append(json.dumps(t.get("result")))
    return spans


# Vault / tool JSON keys and search-down copy — not concept words like occupancy/NOI.
_CRE_HINTS = (
    "unit_count",
    "occupancy_pct",
    "in_place",
    "in-place",
    "vacant_count",
    "mtm_count",
    "display_name",
    "implied_cap",
    "in_place_annual",
    '"lat"',
    '"lon"',
    '"noi"',
    "noi_",
    "unavailable",
)
# Definitional CRE (what is a rent roll) vs this-deal figures (occupancy %, income).
_DEFINITIONAL = re.compile(
    r"(?i)\b(?:what(?:'s| is| are)\s+(?:a |an |the )?|define|explain|"
    r"meaning of|definition of)\b"
)
_DEAL_FIGURE = re.compile(
    r"(?i)\b("
    r"how many|percent|\bpct\b|%|on this|this (?:rent[\s-]*roll|property|deal|asset)|"
    r"in[\s-]*place|monthly income|abstract this|compute|calculat|"
    r"vacant count|unit count|occupied count|implied cap|asking price|"
    r"geocode|on a map|the map|\bmap\b|where is this|where is it"
    r")\b"
)
# Locate / last-pin country or place correction — not a place-name list.
_LOCATE = re.compile(
    r"(?i)(?:"
    r"\bmap\b|"
    r"\bgeocode|"
    r"\bwhere is (?:this|it|the property)\b|"
    r"\bput .{0,40}\bmap\b|"
    r"\blocated\b|"
    r"\blocation\b|"
    r"\bcountry\b|"
    r"\b(?:not|isn't|is not)\s+in\b"
    r")"
)
# Area/market facts — in-scope CRE, not a rent-roll definition or this-roll occupancy.
# Category stems (area/market/zip/city, demographics, hazards, comps); not a place-name list.
_MARKET_AREA = re.compile(
    r"(?i)(?:"
    r"\b(?:that |the |this )?(?:area|metro|market)\b|"
    r"\bzip(?:\s*code)?\b|"
    r"\bcity\b|"
    r"\bdemograph|"
    r"\bhazards?\b|"
    r"average (?:property |home |house |sale |asking )?(?:value|price)s?|"
    r"property values?|(?:sale )?comps?\b|comparables|"
    r"listings?"
    r")"
)
_CHAT_FOLLOW = re.compile(
    r"(?i)\b(?:how are you|what(?:'s| is) your name|who are you|"
    r"hello|hi\b|thanks|thank you|weather)\b"
)
_THIS_ROLL_TERMS = re.compile(
    r"(?i)\b(occupanc|income|units?\b|noi\b|cap rate|rent[\s-]*roll|vacant|mtm)\b"
)


def ask_is_locate(query: str) -> bool:
    """True for map / where / last-pin country or place correction — not occupancy."""
    text = (query or "").replace("\u2019", "'").replace("\u2018", "'").strip()
    if not text or _CHAT_FOLLOW.search(text):
        return False
    if _THIS_ROLL_TERMS.search(text):
        return False
    return bool(_LOCATE.search(text))


def cited_locate_map_draft(query: str, draft: str, traces: list[dict[str, Any]]) -> bool:
    """True when a locate follow-up draft cites this-turn osm_geocode (not occupancy)."""
    if not ask_is_locate(query):
        return False
    text = (draft or "").strip()
    if len(text) < 12:
        return False
    low = text.lower()
    for t in traces or []:
        if str(t.get("name") or "") != "osm_geocode":
            continue
        result = t.get("result")
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        name = str(result.get("display_name") or "").strip()
        lat = result.get("lat")
        lon = result.get("lon")
        if name and name.lower()[:32] in low:
            return True
        if lat is not None and str(lat)[:6] in text:
            return True
        if lon is not None and str(lon)[:6] in text:
            return True
        if "lat" in low and ("lon" in low or "lng" in low):
            return True
        if name and any(part.lower() in low for part in name.split(",")[:2] if len(part.strip()) >= 4):
            return True
    return False


def ask_is_market_area(query: str) -> bool:
    """True for in-scope area/market facts (demographics, hazards, comps), not this-roll occupancy."""
    text = (query or "").replace("\u2019", "'").replace("\u2018", "'").strip()
    if not text or _CHAT_FOLLOW.search(text):
        return False
    if ask_is_locate(text):
        return False
    if _MARKET_AREA.search(text):
        return True
    if _DEAL_FIGURE.search(text) or _DEFINITIONAL.search(text):
        return False
    if re.fullmatch(r"\d{5}(?:-\d{4})?", text):
        return True
    tokens = re.findall(r"[A-Za-z0-9']+", text)
    if 1 <= len(tokens) <= 4 and not _THIS_ROLL_TERMS.search(text):
        return True
    return False


def ask_is_market_comps(query: str) -> bool:
    """Back-compat alias: area/market facts, not listings-only."""
    return ask_is_market_area(query)


def ask_is_conceptual(query: str) -> bool:
    """True when the last user ask is a CRE definition, not this deal's figures.

    Scope (IN_SCOPE vs OOS) is classify_scope on the run_task hop. Do not call
    in_scope here — a second fail-closed LLM hop inside score_gcr made cited
    definitions look like missing vault payload. VAULT HAS is facts, not the ask.
    Area/market facts are in-scope CRE but not a definition — do not route those
    into a rent-roll definition rewrite.
    """
    text = (query or "").replace("\u2019", "'").replace("\u2018", "'").strip()
    if not text:
        return False
    if ask_is_market_area(text) or ask_is_locate(text):
        return False
    if _DEAL_FIGURE.search(text):
        return False
    return bool(_DEFINITIONAL.search(text))


def ask_skips_vault_payload(query: str) -> bool:
    """Definitions, locate/map, and area/market-fact drafts are CRE payload without occupancy %."""
    return ask_is_conceptual(query) or ask_is_market_area(query) or ask_is_locate(query)


def last_prediction_clarify_answer(traces: list[dict[str, Any]] | None) -> str:
    """Last ask_prediction_agent reply if it is a follow-up / not-trained question."""
    from cre_rentroll.predict_client import looks_like_clarification

    last: dict[str, Any] | None = None
    for t in traces or []:
        if str(t.get("name") or "") == "ask_prediction_agent":
            last = t
    if last is None:
        return ""
    result = last.get("result")
    if not isinstance(result, dict):
        return ""
    answer = str(result.get("answer") or "").strip()
    if answer and looks_like_clarification(answer):
        return answer
    return ""


def prediction_clarify_draft(draft: str, traces: list[dict[str, Any]] | None) -> bool:
    """True when the draft is quoting a predictor clarifying / not-trained reply."""
    from cre_rentroll.predict_client import looks_like_clarification

    tool_answer = last_prediction_clarify_answer(traces)
    if not tool_answer:
        return False
    text = (draft or "").strip()
    if len(text) < 12:
        return False
    if looks_like_clarification(text):
        return True
    a, d = tool_answer.lower(), text.lower()
    return a[:60] in d or d[:60] in a


def need_replan_hint(
    query: str, *, vault_label: str | None = None, prediction_clarify: bool = False
) -> str:
    """score_gcr / reflection hint. Definitions/comps must not grow vault calculator lists."""
    if prediction_clarify:
        hint = (
            "need_replan — last ask_prediction_agent is a clarifying question "
            "(which group/plan/variant, untrained / list trained buildings, or "
            "need a named building). Quote that reply as the answer. Do not add "
            "query_units occupancy or vault occupancy %. Completeness is the "
            "follow-up, then score_gcr again"
        )
    elif ask_is_conceptual(query):
        hint = (
            "need_replan — last User message is a CRE definition, not this deal's "
            "location/occupancy/income/NOI/cap. Do not add vault calculators "
            "(query_units, compute_income, compute_noi, compute_cap_rate) or osm_geocode "
            "unless THIS message asked. Rewrite a cited definition (web_search if needed), "
            "then score_gcr again"
        )
    elif ask_is_locate(query):
        hint = (
            "need_replan — last User message is locate/map or a correction of the "
            "last pin's country/place, not occupancy/units/this-roll. Do not add "
            "vault calculators (query_units, compute_income, compute_noi, "
            "compute_cap_rate) and do not rewrite a rent-roll definition or score "
            "G as off-topic rent-roll. web_search then osm_geocode from roll facts "
            "constrained by the last User message; citations/geocode are the payload"
        )
    elif ask_is_market_area(query):
        hint = (
            "need_replan — last User message is an area/market fact "
            "(demographics, hazards, comps), not occupancy/units/this-roll. "
            "Do not add vault calculators "
            "(query_units, compute_income, compute_noi, compute_cap_rate) and do not "
            "rewrite a rent-roll definition or score G as off-topic rent-roll. "
            "Citations/search hits are the payload (web_search if needed); "
            "do not require occupancy numbers, then score_gcr again"
        )
    else:
        hint = (
            "need_replan — call update_plan with NEW remaining work (not create_plan; "
            "completed rows are kept), then those work tools, then score_gcr again"
        )
    extra = (vault_label or "").strip()
    if extra:
        hint = f"{hint}; {extra}"
    return hint


def has_cre_payload(
    draft: str, query: str = "", traces: list[dict[str, Any]] | None = None
) -> bool:
    """True when the draft has CRE JSON/prose to judge — empty is not a payload.

    Conceptual CRE asks (what is a rent roll) need an explanation, not occupancy %
    or income. Area/market facts (demographics, hazards, comps) need a cited figure
    or search hits — not occupancy/units/this-roll JSON. Numeric occupancy-on-this-roll
    still needs vault JSON keys or cited numbers. A predictor clarifying question
    (which group/plan/variant, untrained list) is payload without occupancy %.
    """
    from cre_rentroll.scope import is_refuse_draft

    text = (draft or "").strip()
    if not text:
        return False
    if is_refuse_draft(text):
        return False
    if prediction_clarify_draft(text, traces):
        return True
    if ask_skips_vault_payload(query):
        return len(text) >= 12
    low = text.lower()
    if any(h in low for h in _CRE_HINTS):
        return True
    if _NUM.search(text) and len(text) >= 12:
        return True
    return False


_QUERY_HEADLINE = (
    "unit_count",
    "occupied_count",
    "vacant_count",
    "mtm_count",
    "occupancy_pct",
    "in_place_monthly",
    "avg_sqft",
)
_GEO_HEADLINE = ("lat", "lon")


def _add_num(allowed: set[str], value: Any) -> None:
    if value is None or isinstance(value, bool):
        return
    allowed |= _numbers(str(value))


def _allowed_numbers(roll: CanonicalRentRoll | None, traces: list[dict[str, Any]]) -> set[str]:
    """Headline vault/calc/geo figures only — not unit ids, rents, or query grids.

    Derived math (223−5=218, 252−5=247) must miss this set so vault_calc is N/A,
    not ok and not mismatch.
    """
    allowed: set[str] = set()
    if roll is not None:
        _add_num(allowed, roll.unit_count)
        _add_num(allowed, roll.occupied_count)
        _add_num(allowed, roll.vacant_count)
        _add_num(allowed, roll.mtm_count)
        _add_num(allowed, roll.in_place_monthly)
        _add_num(allowed, roll.avg_sqft)
        occ = float(roll.occupancy_pct)
        _add_num(allowed, occ)
        if 0 < occ <= 1.0000001:
            _add_num(allowed, round(occ * 100, 2))
    for span in this_turn_calc_spans(traces):
        allowed |= _numbers(span)
    for t in traces:
        name = str(t.get("name") or "")
        result = t.get("result")
        if not isinstance(result, dict):
            continue
        if name == "query_units":
            for key in _QUERY_HEADLINE:
                _add_num(allowed, result.get(key))
        elif name == "osm_geocode":
            for key in _GEO_HEADLINE:
                _add_num(allowed, result.get(key))
        elif name == "ask_vacancy_agent":
            for plan in result.get("plans") or []:
                if not isinstance(plan, dict):
                    continue
                _add_num(allowed, plan.get("units"))
                _add_num(allowed, plan.get("vacant"))
                _add_num(allowed, plan.get("probability"))
        elif name == "ask_prediction_agent":
            allowed |= _numbers(str(result.get("answer") or ""))
    return _with_percent_twins(allowed)


def _date_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for rx in _DATE_RES:
        for m in rx.finditer(text or ""):
            spans.append((m.start(), m.end()))
    return spans


def _in_date_span(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < hi and end > lo for lo, hi in spans)


def _sentence_excerpt(text: str, start: int, end: int) -> str:
    lo, hi = start, end
    while lo > 0 and text[lo - 1] not in ".\n!?":
        lo -= 1
    while hi < len(text) and text[hi] not in ".\n!?":
        hi += 1
    if hi < len(text) and text[hi] in ".!?":
        hi += 1
    snippet = re.sub(r"\s+", " ", text[lo:hi]).strip(" \t\"'")
    return snippet or re.sub(r"\s+", " ", text[start:end]).strip()


def _fmt_headline_num(n: float | int) -> str:
    f = float(n)
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    s = f"{round(f, 4):.4f}".rstrip("0").rstrip(".")
    return s


def vault_headline_nums(roll: CanonicalRentRoll | None) -> str:
    """CRE headline figures for operator logs — not the full unit grid."""
    if roll is None:
        return ""
    occ = float(roll.occupancy_pct)
    occ_s = f"{occ * 100:.2f}%" if occ <= 1.0000001 else f"{occ:.2f}%"
    return ", ".join(
        [
            _fmt_headline_num(roll.unit_count),
            _fmt_headline_num(roll.occupied_count),
            _fmt_headline_num(roll.vacant_count),
            _fmt_headline_num(roll.in_place_monthly),
            _fmt_headline_num(roll.mtm_count),
            occ_s,
        ]
    )


def vault_calc_invented_hits(
    draft: str, roll: CanonicalRentRoll | None, traces: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Draft number tokens (keep %) with excerpt; calendar dates are not CRE figures."""
    if not (draft or "").strip():
        return []
    allowed = _allowed_numbers(roll, traces)
    date_spans = _date_spans(draft)
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in _NUM_TOKEN.finditer(draft):
        if _in_date_span(m.start(), m.end(), date_spans):
            continue
        tok = m.group(0)
        try:
            v = float(tok.replace(",", "").rstrip("%"))
        except ValueError:
            continue
        if v in _STRUCTURAL:
            continue
        n = f"{v:.4f}"
        if n in allowed or any(_num_close(v, float(a)) for a in allowed):
            continue
        if tok not in seen:
            seen.add(tok)
            hits.append({"token": tok, "excerpt": _sentence_excerpt(draft, m.start(), m.end())})
    return hits


def vault_calc_invented(
    draft: str, roll: CanonicalRentRoll | None, traces: list[dict[str, Any]]
) -> list[str]:
    """Draft number tokens (keep %) with no vault/tool span match. Short list, no grid."""
    return [h["token"] for h in vault_calc_invented_hits(draft, roll, traces)]


def vault_calc_log_label(
    ok: bool | None,
    invented: list[str] | list[dict[str, str]] | None = None,
    *,
    draft: str | None = None,
    roll: CanonicalRentRoll | None = None,
) -> str:
    # None = cited number not in allowlist (derived math, extra figure). Not a fail.
    if ok is None:
        return "N/A"
    if ok:
        return "ok"
    hits: list[dict[str, str]] = []
    for item in invented or []:
        if isinstance(item, dict):
            tok = str(item.get("token") or "")
            excerpt = str(item.get("excerpt") or "")
        else:
            tok = str(item)
            excerpt = ""
        if not tok:
            continue
        if not excerpt and draft:
            m = re.search(re.escape(tok), draft)
            if m:
                excerpt = _sentence_excerpt(draft, m.start(), m.end())
        hits.append({"token": tok, "excerpt": excerpt})
    if not hits:
        return "mismatch"
    bits: list[str] = []
    for h in hits:
        if h["excerpt"]:
            bits.append(f'{h["token"]} from "{h["excerpt"]}"')
        else:
            bits.append(h["token"])
    verb = "is" if len(bits) == 1 else "are"
    headline = vault_headline_nums(roll)
    where = f"vault ({headline})" if headline else "vault spans"
    return f"mismatch — {'; '.join(bits)} {verb} not in {where}"


def format_fail_why(scores: EvalScores, decision: str | None = None) -> str:
    """Operator WHY for need_replan / low G/C/R / mismatch / judge error.

    not_run is N/A (score_gcr never ran) — not a score_gcr fail reason.
    """
    judge = scores.judge or "not_run"
    if judge == "not_run":
        return ""
    if decision in {"ok", "show"}:
        return ""
    bits: list[str] = []
    failed: list[str] = []
    if isinstance(scores.G, int) and scores.G <= 2:
        failed.append(f"G={scores.G}")
    if isinstance(scores.request_C, int) and scores.request_C <= 2:
        failed.append(f"request-C={scores.request_C}")
    if isinstance(scores.R, int) and scores.R <= 2:
        failed.append(f"R={scores.R}")
    if failed:
        bits.append("failed " + ", ".join(failed))
    comment = re.sub(r"\s+", " ", (scores.reason or "")).strip()
    if comment and comment not in bits:
        bits.append(comment)
    return "; ".join(bits)


def append_fail_why(line: str, why: str) -> str:
    why = re.sub(r"\s+", " ", (why or "")).strip()
    if not why:
        return line
    if why in line:
        return line
    return f"{line} — {why}"


def format_eval_log(scores: EvalScores, decision: str) -> str:
    """Log stored scores only. Never invent G/C/R or vault mismatch."""
    vault_label = getattr(scores, "vault_calc", None) or vault_calc_log_label(scores.vault_calc_ok)
    judge = scores.judge or "not_run"
    if judge == "error":
        line = (
            f"live OpenAI JSON judge failed; vault_calc={vault_label} judge=error → {decision}"
        )
    elif judge in {"not_run", "empty"}:
        line = f"G=N/A C=N/A R=N/A vault_calc={vault_label} judge={judge} → {decision}"
    else:
        g = "N/A" if scores.G is None else str(scores.G)
        c = "N/A" if scores.request_C is None else str(scores.request_C)
        r = "N/A" if scores.R is None else str(scores.R)
        line = f"G={g} request-C={c} R={r} vault_calc={vault_label} judge={judge} → {decision}"
    return append_fail_why(line, format_fail_why(scores, decision))


def vault_calc_ok(
    draft: str, roll: CanonicalRentRoll | None, traces: list[dict[str, Any]]
) -> bool | None:
    """Allowlist check only. Cannot tell 'wrong vault field' vs derived math.

    True: every cited number is a headline vault/calc/geo allowlist value.
    None: a cited number is absent (derived math, extra figure) — N/A, not ok,
    not mismatch. Never returns False (unknown is not a contradiction).
    """
    if not (draft or "").strip():
        return None
    nums = _numbers(draft)
    if not nums:
        return True
    if vault_calc_invented(draft, roll, traces):
        return None
    return True


JUDGE_SYS = """You are a CRE rent-roll eval judge. Score integers 1-5. Return JSON only: {"R":n,"G":n,"request_C":n,"reason":"short why"}

R (relevance): Is the answer on-topic for this user question? High if it addresses the CRE ask. High if the user asked identity, greetings, weather, sports, recipes, or other non-deal chat and the answer is the rent-roll-only refuse. Low (1-2) if a CRE ask gets off-topic chatter, or if a non-deal ask gets a CRE/tool answer instead of the refuse.
- Any in-scope area/market question (demographics, hazards, comps, average property value, listings) is on-topic CRE. High R if the answer cites search hits or a stated area/market figure. Citations/search hits are the payload. Do not score R=1 as off-topic / "not a rent-roll question" for omitting occupancy, units, this-roll payload, or a rent-roll definition. Listing URLs are allowed.
- Locate / map / where is this property / the user correcting the last geocode's country or place / search the map again is on-topic locate for THIS deal. High R if the answer cites osm_geocode, search hits, or FACT# street. Do not score R=1 as off-topic / "not a rent-roll question".
- If the last ask_prediction_agent tool JSON is a clarifying question (which group/plan/variant; untrained / list trained buildings; need a named building), quoting that reply is on-topic for THIS turn. High R. Do not low-R because the user asked occupancy and the answer lists plans instead of a predicted rate.

G (groundedness): Are numeric and factual claims supported by VAULT / tools / FACT# / RollAbstract numbers, or by arithmetic from those plus rents the USER stated in QUERY?
- Identity, greetings, weather, or other non-deal chat: the rent-roll-only refuse is grounded. Do not low-G it for omitting vault numbers.
- Conceptual / definitional CRE (what is a rent roll, occupancy, NOI): an on-topic explanation grounded in search hits / FACT# / tools is grounded. Do not low-G for omitting occupancy %, income, unit counts, or other vault numbers when the user did not ask for this deal's figures.
- Area/market facts (demographics, hazards, comps, average property value): a cited figure and search-hit / FACT# / tool citations are grounded. Do not low-G or G=1 "not a rent-roll question" for omitting occupancy %, income, unit counts, or this-roll vault numbers. Do not require occupancy numbers.
- Locate / map / last-pin country or place correction: a cited osm_geocode / search-hit / FACT# street (display_name, lat, lon) is grounded. Do not low-G or G=1 "not a rent-roll question" / off-topic for omitting occupancy %, income, unit counts, or this-roll occupancy payload. Do not require occupancy numbers.
- Predictor clarifying question (tool JSON is which group/plan/variant, untrained / list trained buildings, or need a named building): quoting that tool reply is grounded. Do not low-G for omitting occupancy_pct / in_place_monthly. Do not treat query_units occupancy as the required grounding.
- Rate fraction vs percent is the SAME figure: occupancy_pct 0.9207 and 92.07%, implied_cap 0.2908 and 29.08% (also 29.1% or 29%) are both grounded. Do not lower G for converting 0.080 ↔ 8.0%, 0.9207 ↔ 92.07%, or rounding a displayed percent. Distinct rates (8% vs 29%) are not the same figure.
- If the user asked only for vacant count (vacant-only), do not lower G for omitting occupancy, income, NOI, or unit mix.
- User-stated scenario rents (e.g. vacant units at $5,000/month) are grounded in QUERY. vacant_count × that rent × 12, and in_place_annual + that fill, are grounded. Do not low-G a full-occupancy total for including occupied in-place.
- Invented numbers not in vault, tool JSON, FACT#, RollAbstract, QUERY, or that arithmetic → low G.

C (request completeness — not gold.json): Did the answer cover what was asked this turn?
- Identity, greetings, weather, or other non-deal chat: the rent-roll-only refuse is complete. Do not low-C it for omitting a name or a weather forecast.
- Conceptual / definitional CRE: a correct cited explanation is complete. Do not require vault occupancy/income/unit figures on those asks.
- Area/market facts: a cited figure and/or search hits / listing links is complete. Do not require occupancy/units/this-roll payload or a rent-roll definition. Do not fail C as "not a rent-roll question".
- Locate / map / last-pin country or place correction: a cited geocode/map sentence is complete. Do not require occupancy/units/this-roll payload or a rent-roll definition. Do not fail C as "not a rent-roll question".
- Predictor clarifying question: if the last ask_prediction_agent result is a clarifying question (which group/plan/variant; untrained / list trained buildings; need a named building), that quoted reply IS a complete, relevant, grounded answer for THIS user turn. Completeness is not "must include occupancy % from the vault". Do not ding C for omitting occupancy_pct / in_place_monthly. Do not treat "user asked occupancy, model listed plans" as incomplete when the tool JSON is a clarify-from-predictor. Do not suggest substituting query_units occupancy.
- Vacant-only questions do NOT require occupancy %, income, geocode, or NOI.
- Occupied-only in-place monthly is not 100% occupancy / all-units-occupied / no-vacant income. Repeating in_place_monthly when the user asked for full occupancy (or said the dollar figure is the same as with vacancy) is low C. Occupancy counts are not a substitute for that income estimate.
- If the user asked income if ALL units are occupied and gave a fill rent for vacant units, the complete figure is occupied in-place plus vacant fill (annualize if they asked annual). Do not fail C for reporting that total. Do not treat vacant-fill-only ($780k) as the whole ask unless they asked only for the vacant increment.
- Do not require lat/lon unless the user asked location / map / geocode / corrected the last pin.
- Do not treat CI gold fields as required.

Judge only from QUERY, ANSWER, and FACTS. Do not use DeepEval.
reason: if any of G/C/R is 1-2 or the answer invents numbers, say which axis failed and cite vault/tool figures. Optional notes / justification / invented fields are merged into reason. Do not quote the user question, interview questions, or gold.json."""


def _clamp_score(n: Any) -> int:
    if n is None or isinstance(n, bool):
        raise ValueError("invalid judge score")
    v = int(n)
    return max(1, min(5, v))


def _compact_tool_result(name: str, result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    if name == "query_units":
        slim = {k: v for k, v in result.items() if k != "units"}
        units = result.get("units") or []
        slim["unit_rows"] = len(units) if isinstance(units, list) else 0
        return slim
    if name == "compute_cap_rate":
        slim = dict(result)
        cap = slim.get("implied_cap")
        try:
            cv = float(cap)
        except (TypeError, ValueError):
            cv = None
        if cv is not None and 0 < cv <= 1.0000001:
            slim["implied_cap_pct"] = round(cv * 100.0, 2)
        return slim
    return result


def _ltm_facts() -> dict[str, Any]:
    try:
        from cre_rentroll import turn_ctx
        from cre_rentroll.memory import retrieve_facts

        user = turn_ctx.username()
        if not user:
            return {}
        return retrieve_facts(user)
    except Exception:
        return {}


def _judge_facts(roll: CanonicalRentRoll | None, traces: list[dict[str, Any]]) -> dict[str, Any]:
    facts: dict[str, Any] = {"tools": [], "roll_abstract": None, "vault_spans": [], "fact_slots": {}}
    if roll is not None:
        occ = float(roll.occupancy_pct)
        facts["roll_abstract"] = {
            "property_name": roll.property_name,
            "unit_count": roll.unit_count,
            "occupied_count": roll.occupied_count,
            "vacant_count": roll.vacant_count,
            "occupancy_pct": occ,
            "occupancy_pct_display": f"{occ * 100:.2f}%",
            "in_place_monthly": roll.in_place_monthly,
            "mtm_count": roll.mtm_count,
            "as_of": roll.as_of,
        }
        facts["vault_spans"] = roll.vault_spans()
    for t in traces:
        name = str(t.get("name") or "")
        facts["tools"].append({"name": name, "result": _compact_tool_result(name, t.get("result"))})
    facts["fact_slots"] = _ltm_facts()
    if last_prediction_clarify_answer(traces):
        facts["predictor_clarify"] = True
        abstract = facts.get("roll_abstract")
        if isinstance(abstract, dict):
            for key in ("occupancy_pct", "occupancy_pct_display", "in_place_monthly"):
                abstract.pop(key, None)
        facts["completeness"] = (
            "Last ask_prediction_agent is a clarifying question. That quoted reply "
            "completes THIS turn. Do not require occupancy_pct or in_place_monthly. "
            "Do not substitute query_units occupancy."
        )
    return facts


def _judge_error_reason(exc: BaseException) -> str:
    msg = re.sub(r"\s+", " ", str(exc) or type(exc).__name__).strip()
    msg = re.sub(r"sk-[A-Za-z0-9]+", "sk-***", msg)
    return f"{type(exc).__name__}: {msg}"[:300]


def _strip_query_leak(text: str, query: str) -> str:
    """Keep interview / user questions off operator logs."""
    out = text or ""
    q = re.sub(r"\s+", " ", (query or "")).strip()
    if q and len(q) >= 12 and q in out:
        out = out.replace(q, "[query]")
    return out


def _judge_comment(data: dict[str, Any], query: str = "") -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for key in ("reason", "notes", "justification"):
        val = data.get(key)
        chunks: list[str] = []
        if isinstance(val, str) and val.strip():
            chunks.append(val.strip())
        elif isinstance(val, list):
            chunks.extend(str(x).strip() for x in val if str(x).strip())
        for chunk in chunks:
            norm = re.sub(r"\s+", " ", chunk).strip()
            if norm and norm not in seen:
                seen.add(norm)
                parts.append(norm)
    invented = data.get("invented") or data.get("invented_numbers")
    if isinstance(invented, list) and invented:
        nums = ", ".join(str(x) for x in invented[:8] if str(x).strip())
        if nums:
            tag = f"invented {nums}"
            if tag not in seen:
                parts.append(tag)
    return _strip_query_leak("; ".join(parts), query)[:500]


def live_judge(query: str, answer: str, roll: CanonicalRentRoll | None, traces: list[dict[str, Any]]) -> EvalScores:
    """Live R / G / request-C from OpenAI JSON judge only. CompletenessEvaluator is CI only.

    vault_calc_ok is True when cited numbers match the allowlist, else None (N/A).
    Allowlist miss never fails the gate. On judge failure (no key, timeout, bad JSON,
    OpenAI error) G/C/R stay empty — never heuristics.
    """
    from cre_rentroll.settings import openai_api_key

    if not has_cre_payload(answer, query, traces):
        # Numeric occupancy-on-this-roll with no vault figures — G/C/R stay N/A.
        # Conceptual CRE, area/market-fact, and predictor-clarify drafts are
        # payload without occupancy %.
        return EvalScores(judge="empty", reason="draft missing CRE payload")
    ok = vault_calc_ok(answer, roll, traces)
    if not openai_api_key():
        return EvalScores(vault_calc_ok=ok, judge="error", reason="OpenAI API key missing")
    try:
        r, g, c, reason = _llm_scores(query, answer, roll, traces)
        return EvalScores(R=r, G=g, request_C=c, vault_calc_ok=ok, judge="llm", reason=reason or None)
    except Exception as exc:
        return EvalScores(vault_calc_ok=ok, judge="error", reason=_judge_error_reason(exc))


def _llm_scores(
    query: str, answer: str, roll: CanonicalRentRoll | None, traces: list[dict[str, Any]]
) -> tuple[int, int, int, str]:
    from openai import OpenAI

    from cre_rentroll.settings import openai_classify_model, openai_client_args

    client = OpenAI(**openai_client_args())
    facts = _judge_facts(roll, traces)
    note = ""
    if facts.get("predictor_clarify"):
        note = (
            "NOTE: Last ask_prediction_agent is a clarifying question. Score the "
            "quoted reply as complete, relevant, and grounded for THIS turn. Do not "
            "require occupancy_pct or in_place_monthly. Do not substitute query_units "
            "occupancy.\n"
        )
    user = (
        f"{note}QUERY: {query}\nANSWER: {answer}\nFACTS: {json.dumps(facts, default=str)[:8000]}"
    )
    resp = client.chat.completions.create(
        model=openai_classify_model(),
        messages=[
            {"role": "system", "content": JUDGE_SYS},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=240,
    )
    from cre_rentroll.turn_ctx import add_llm_from_response

    add_llm_from_response("judge", resp)
    raw = resp.choices[0].message.content
    if not raw or not str(raw).strip():
        raise ValueError("empty judge response")
    data = json.loads(raw)
    if not isinstance(data, dict) or not all(k in data for k in ("R", "G", "request_C")):
        raise ValueError("judge JSON missing scores")
    reason = _judge_comment(data, query)
    return _clamp_score(data["R"]), _clamp_score(data["G"]), _clamp_score(data["request_C"]), reason


def gate(scores: EvalScores, retry_used: bool) -> str:
    """Return next: retry | error | warn | show."""
    if scores.judge in {"error", "empty", "not_run"}:
        return "error"
    if scores.G is None and scores.request_C is None:
        return "error"
    # False only: genuine contradiction if a caller sets it. None (N/A / not in
    # allowlist) must not retry or error — G/C/R still apply.
    if scores.vault_calc_ok is False:
        return "error" if retry_used else "retry"
    g = scores.G or 0
    c = scores.request_C or 0
    low = min(g, c)
    if low <= 2:
        return "retry" if not retry_used else "error"
    if low == 3 or g == 3 or c == 3:
        return "warn"
    return "show"


def ci_aggregate(runs: list[dict[str, float]]) -> dict[str, Any]:
    def stats(key: str) -> dict[str, float]:
        vals = [float(r[key]) for r in runs]
        return {"mean": mean(vals), "stdev": stdev(vals) if len(vals) > 1 else 0.0}

    return {"G": stats("G"), "C": stats("C"), "R": stats("R"), "n": len(runs)}
