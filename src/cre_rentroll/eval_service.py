from __future__ import annotations

from cre_rentroll.questions import QUESTIONS
from cre_rentroll.schemas import CanonicalRentRoll


def score(
    *,
    qid: str,
    roll: CanonicalRentRoll,
    answer: str,
    tools_used: list[str],
    evidence_ids: list[str],
) -> dict:
    """G/C/R. Offline can use roll totals as gold; production omits gold and uses traces."""
    spec = QUESTIONS[qid]
    tools = set(tools_used)
    ev = set(evidence_ids)

    # Groundedness: claims need evidence ids from this run (no gold answer required).
    if spec.get("scope") == "irrelevant":
        grounded = 1.0 if not tools else 0.0
        complete = 1.0
        relevant = 1.0 if not tools else 0.3
        return _pack(qid, grounded, complete, relevant, tools, "scope_reject")

    grounded = 1.0 if ev else 0.0
    if "calc:" in " ".join(ev) or "unit:" in " ".join(ev) or "geo:" in " ".join(ev) or "web:" in " ".join(ev):
        grounded = 1.0

    complete = 1.0
    for need in spec["required"]:
        if need == "osm" and "osm" not in tools and "geo:osm" not in ev:
            complete = min(complete, 0.4)
        if need == "web_search" and "web_search" not in tools and "web:search" not in ev:
            complete = min(complete, 0.4)
        if need in {"calc", "occupancy", "income", "vault_or_calc"}:
            if not any(x.startswith("calc:") or x.startswith("unit:") for x in ev):
                complete = min(complete, 0.5)

    forbidden_hit = [f for f in spec["forbidden"] if f in tools]
    relevant = 0.4 if forbidden_hit else 1.0

    return _pack(qid, grounded, complete, relevant, tools, ";".join(forbidden_hit) or "ok")


def _pack(qid, g, c, r, tools, note) -> dict:
    return {
        "qid": qid,
        "groundedness": g,
        "completeness": c,
        "relevance": r,
        "tools_used": sorted(tools),
        "note": note,
        "gold_used": False,
        "how": (
            "Groundedness = evidence ids on this trace (spans/URLs/calc). "
            "Completeness = required capabilities for this question. "
            "Relevance = no forbidden tools. Gold file is optional (CI only)."
        ),
    }
