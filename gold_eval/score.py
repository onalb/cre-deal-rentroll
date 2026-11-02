"""Gold accuracy and best-case tool-call checks. The live judge still owns G, C, and R."""

from __future__ import annotations

import re
from typing import Any

_WORK_TOOLS = frozenset(
    {
        "query_units",
        "compute_income",
        "compute_noi",
        "compute_cap_rate",
        "ask_prediction_agent",
        "osm_geocode",
        "web_search",
    }
)

_LABELS = {
    "unit_count": "unit count",
    "occupancy_pct": "occupancy",
    "in_place_monthly": "in-place monthly",
    "vacant_count": "vacant count",
    "mtm_count": "month-to-month",
    "place": "place",
}

_STATE_ZIP = re.compile(r"\s+\b[A-Za-z]{2}\b(?:\s+\d{5}(?:-\d{4})?)?\s*$")
_ZIP = re.compile(r"\s+\d{5}(?:-\d{4})?\s*$")


def work_tools() -> frozenset[str]:
    return _WORK_TOOLS


def _times(name: str, n: int) -> str:
    return name if n == 1 else f"{name} ×{n}"


def score_tool_calls(case: dict[str, Any], called: list[str]) -> dict[str, Any]:
    """Count work-tool calls against the case budget. ``score_gcr`` is ignored."""
    best = {str(name): int(n) for name, n in (case.get("best_calls") or {}).items()}
    forbidden = {str(name) for name in (case.get("forbidden_tools") or [])}
    counts: dict[str, int] = {}
    for name in called:
        tool = str(name)
        if tool not in _WORK_TOOLS:
            continue
        counts[tool] = counts.get(tool, 0) + 1
    missing: list[str] = []
    extra: list[str] = []
    for name, need in best.items():
        got = counts.get(name, 0)
        if got < need:
            missing.append(_times(name, need - got))
        elif got > need:
            extra.append(_times(name, got - need))
    for name, got in counts.items():
        if name not in best and got:
            extra.append(_times(name, got))
    banned = sorted(forbidden & set(counts))
    used = sum(counts.values())
    budget = sum(best.values())
    return {
        "ok": not missing and not extra and not banned,
        "used": used,
        "budget": budget,
        "counts": counts,
        "missing": missing,
        "extra": extra,
        "forbidden_used": banned,
    }


def _answer_has_number(text: str, value: float) -> bool:
    from cre_rentroll.eval_service import _NUM_TOKEN, _date_spans, _in_date_span, _num_close

    target = float(value)
    spans = _date_spans(text or "")
    for match in _NUM_TOKEN.finditer(text or ""):
        if _in_date_span(match.start(), match.end(), spans):
            continue
        token = match.group(0).replace(",", "").rstrip("%")
        try:
            got = float(token)
        except ValueError:
            continue
        if _num_close(got, target):
            return True
    return False


def _phrase_in(hay: str, needle: str) -> bool:
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle.casefold())}(?!\w)", hay) is not None


def _city(segment: str) -> str:
    city = _STATE_ZIP.sub("", segment).strip()
    return _ZIP.sub("", city).strip()


def place_hit(answer: str, address: str | None) -> bool:
    """True when the answer names the roll street or the roll city."""
    raw = re.sub(r"\s+", " ", str(address or "")).strip()
    if not raw:
        return False
    hay = re.sub(r"\s+", " ", answer or "").casefold()
    if raw.casefold() in hay:
        return True
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    street = parts[0] if parts else raw
    if len(street) >= 8 and _phrase_in(hay, street):
        return True
    for part in parts[1:]:
        city = _city(part)
        if len(city) >= 4 and not re.fullmatch(r"[A-Za-z]{2}", city) and _phrase_in(hay, city):
            return True
    return False


def _display_value(field: str, value: Any) -> str:
    if field == "place":
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text or "—"
    if value is None:
        return "—"
    number = float(value)
    if field == "occupancy_pct":
        shown = number * 100.0 if 0 <= number <= 1.0000001 else number
        return f"{shown:.2f}%"
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text


def _fact(field: str, hit: bool, expected: str = "") -> dict[str, Any]:
    return {
        "id": field,
        "label": _LABELS.get(field, field),
        "hit": hit,
        "expected": expected,
    }


def _gold_answer(facts: list[dict[str, Any]]) -> str:
    parts = [f"{fact['label']} {fact['expected']}" for fact in facts if fact.get("expected")]
    return " · ".join(parts)


def score_accuracy(case: dict[str, Any], answer: str, roll: Any) -> dict[str, Any]:
    """Pass only when every gold fact is in the answer. No 1–5 score."""
    expect = case.get("expect") or {}
    fields = [str(name) for name in (expect.get("from_roll") or [])]
    place_from = str(expect.get("place_from") or "").strip()
    if not fields and not place_from:
        return {"pass": None, "facts": [], "gold_answer": ""}
    facts: list[dict[str, Any]] = []
    for field in fields:
        if roll is None or not hasattr(roll, field):
            facts.append(_fact(field, False, "—"))
            continue
        value = getattr(roll, field)
        if field == "mtm_count" and not value:
            continue
        if value is None:
            facts.append(_fact(field, False, "—"))
            continue
        facts.append(_fact(field, _answer_has_number(answer, float(value)), _display_value(field, value)))
    if place_from:
        address = None if roll is None else getattr(roll, place_from, None)
        facts.append(_fact("place", place_hit(answer, address), _display_value("place", address)))
    if not facts:
        return {"pass": None, "facts": [], "gold_answer": ""}
    return {
        "pass": all(bool(fact["hit"]) for fact in facts),
        "facts": facts,
        "gold_answer": _gold_answer(facts),
    }


def slim_work_trace(traces: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Work-tool calls in order, without the unit grid."""
    out: list[dict[str, Any]] = []
    for row in traces or []:
        name = str(row.get("name") or "")
        if name not in _WORK_TOOLS:
            continue
        out.append(
            {
                "name": name,
                "args": row.get("args") if isinstance(row.get("args"), dict) else {},
                "result": _slim_result(row.get("result")),
            }
        )
    return out


def _slim_result(result: Any) -> Any:
    if isinstance(result, dict):
        slim: dict[str, Any] = {}
        for key, value in result.items():
            if key == "units":
                slim["unit_rows"] = len(value) if isinstance(value, list) else 0
                continue
            if isinstance(value, str) and len(value) > 500:
                slim[key] = value[:499] + "…"
                continue
            slim[key] = value
        return slim
    if isinstance(result, str) and len(result) > 500:
        return result[:499] + "…"
    return result
