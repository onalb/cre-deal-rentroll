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


def _num_close(v: float, a: float) -> bool:
    """Same figure after rounding, or occupancy-style 0.9207 vs 92.07%."""
    if abs(v - a) < 0.51:
        return True
    scale = max(abs(v), abs(a), 1e-9)
    if abs(v - a) / scale < 1e-3:
        return True
    lo, hi = (v, a) if abs(v) <= abs(a) else (a, v)
    if 0 < lo <= 1.0 and 1.0 < hi <= 100.5:
        scaled = lo * 100.0
        if abs(scaled - hi) < 0.51:
            return True
        if abs(scaled - hi) / max(abs(scaled), abs(hi), 1e-9) < 1e-3:
            return True
        if abs(round(scaled, 2) - round(hi, 2)) < 1e-9:
            return True
    return False


def this_turn_calc_spans(traces: list[dict[str, Any]]) -> list[str]:
    spans: list[str] = []
    for t in traces:
        if t.get("name") not in {"compute_income", "compute_noi", "compute_cap_rate"}:
            continue
        spans.append(json.dumps(t.get("result")))
    return spans


_CRE_HINTS = (
    "unit_count",
    "occupancy",
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
    " noi",
    "noi_",
    "cap rate",
    "multifamily",
    "unavailable",
)


def has_cre_payload(draft: str) -> bool:
    """True when the draft has CRE JSON/prose to judge — empty is not a payload."""
    text = (draft or "").strip()
    if not text:
        return False
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
    return allowed


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

R (relevance): Is the answer on-topic for this user question? High if it addresses the CRE ask. Low (1-2) if off-topic (weather, sports, small talk) or out of scope chatter.

G (groundedness): Are numeric and factual claims supported by VAULT / tools / FACT# / RollAbstract numbers, or by arithmetic from those plus rents the USER stated in QUERY?
- Occupancy fraction vs percent is the SAME figure: occupancy_pct 0.9207 and 92.07% are both grounded. Do not lower G for converting 0.9207 ↔ 92.07% or rounding a displayed percent.
- If the user asked only for vacant count (vacant-only), do not lower G for omitting occupancy, income, NOI, or unit mix.
- User-stated scenario rents (e.g. vacant units at $5,000/month) are grounded in QUERY. vacant_count × that rent × 12, and in_place_annual + that fill, are grounded. Do not low-G a full-occupancy total for including occupied in-place.
- Invented numbers not in vault, tool JSON, FACT#, RollAbstract, QUERY, or that arithmetic → low G.

C (request completeness — not gold.json): Did the answer cover what was asked this turn?
- Vacant-only questions do NOT require occupancy %, income, geocode, or NOI.
- Occupied-only in-place monthly is not 100% occupancy / all-units-occupied / no-vacant income. Repeating in_place_monthly when the user asked for full occupancy (or said the dollar figure is the same as with vacancy) is low C. Occupancy counts are not a substitute for that income estimate.
- If the user asked income if ALL units are occupied and gave a fill rent for vacant units, the complete figure is occupied in-place plus vacant fill (annualize if they asked annual). Do not fail C for reporting that total. Do not treat vacant-fill-only ($780k) as the whole ask unless they asked only for the vacant increment.
- Do not require lat/lon unless the user asked location / map / geocode.
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

    if not has_cre_payload(answer):
        # No CRE to judge — G/C/R stay N/A; vault_calc was not run.
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
    user = (
        f"QUERY: {query}\nANSWER: {answer}\nFACTS: {json.dumps(facts, default=str)[:8000]}"
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
