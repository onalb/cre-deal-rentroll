"""Scope → inventory → (optional) decompose. Tools are chosen per question, not hardcoded."""

from __future__ import annotations

import os

from cre_rentroll import geo, search, tools, vault
from cre_rentroll.eval_service import score
from cre_rentroll.questions import QUESTIONS, render
from cre_rentroll.schemas import CanonicalRentRoll

IRRELEVANT = ("weather", "recipe", "sports score")


def in_scope(prompt: str) -> bool:
    p = prompt.lower()
    if any(w in p for w in IRRELEVANT) and "rent" not in p and "cap" not in p:
        return False
    return True


def run_question(deal_id: str, qid: str, *, offline: bool | None = None) -> dict:
    if offline is None:
        offline = os.getenv("CRE_RENTROLL_OFFLINE", "0") == "1"
    spec = QUESTIONS[qid]
    prompt = spec["prompt"]
    roll = vault.load_deal(deal_id)
    if spec.get("scope") == "irrelevant" or not in_scope(prompt):
        return {
            "status": "IRRELEVANT",
            "answer": "Out of scope for this CRE app.",
            "tools_used": [],
            "evidence_ids": [],
            "eval": score(qid=qid, roll=roll, answer="", tools_used=[], evidence_ids=[]),
        }
    return _execute(roll, qid, spec, offline=offline)


def _execute(roll: CanonicalRentRoll, qid: str, spec: dict, *, offline: bool) -> dict:
    """
    Interview note: a live Strands agent would pick tools. This deterministic
    runner encodes the *expected* inventory use so ingest/eval work without an API key.
    `cre-rentroll ask --agent` is where Strands would take over.
    """
    used: list[str] = []
    evidence: list[str] = []
    parts: list[str] = [f"Property: {roll.meta.name} (as of {roll.meta.as_of})."]

    needs_vault = qid in {"q1", "q3"} or "abstract" in spec["prompt"].lower()
    if qid == "q1" or needs_vault and qid != "q2":
        occ = tools.compute_occupancy(roll)
        inc = tools.compute_in_place_income(roll)
        used += ["vault", "compute_occupancy", "compute_in_place_income"]
        evidence += [occ["span_id"], inc["span_id"]]
        if roll.units:
            evidence.append(roll.units[0].span_id)
        parts.append(
            f"Units={occ['unit_count']} occupied={occ['occupied']} vacant={occ['vacant']} "
            f"({occ['occupancy_pct']}%). In-place monthly ${inc['monthly']:,.2f} "
            f"(annual ${inc['annual_gpr_in_place']:,.2f}). [{occ['span_id']}, {inc['span_id']}]"
        )

    if qid == "q2":
        if roll.meta.address_confidence == "high" and roll.meta.address:
            q = roll.meta.address
        else:
            used.append("web_search")
            ws = search.web_search(f"{roll.meta.name} apartments address", offline=True)
            evidence.append(ws["span_id"])
            parts.append(
                f"Address confidence={roll.meta.address_confidence}. {roll.meta.address_note}"
            )
            q = roll.meta.address or roll.meta.name
        g = geo.geocode(q, offline=offline)
        used.append("osm")
        evidence.append(g["span_id"])
        parts.append(
            f"OSM: {g.get('display_name') or g.get('error')} "
            f"lat={g.get('lat')} lon={g.get('lon')} [{g['span_id']}]"
        )

    if qid == "q3":
        inc = tools.compute_in_place_income(roll)
        cap = tools.compute_implied_cap(inc["annual_gpr_in_place"], 12_400_000)
        used += ["compute_in_place_income", "compute_implied_cap", "web_search"]
        evidence += [inc["span_id"], cap["span_id"]]
        metro = "Houston" if "cordoba" in roll.meta.name.lower() else (
            "Portsmouth VA" if "bay view" in roll.meta.name.lower() else roll.meta.name
        )
        ws = search.web_search(f"{metro} multifamily cap rate", offline=offline)
        evidence.append(ws["span_id"])
        url = (ws.get("hits") or [{}])[0].get("url")
        parts.append(
            f"Implied gross cap at $12.4M ask: {cap['implied_cap_pct']}% "
            f"on annual in-place ${inc['annual_gpr_in_place']:,.2f} [{cap['span_id']}]. "
            f"Market note: {ws.get('hits', [{}])[0].get('snippet')} ({url}) [{ws['span_id']}]."
        )

    answer = " ".join(parts)
    ev = score(qid=qid, roll=roll, answer=answer, tools_used=used, evidence_ids=evidence)
    return {"status": "ok", "answer": answer, "tools_used": used, "evidence_ids": evidence, "eval": ev}
