"""Native tools: rent-roll math and vault lookup. External map/search live in geo.py / search.py."""

from __future__ import annotations

from cre_rentroll.schemas import CanonicalRentRoll


def compute_in_place_income(roll: CanonicalRentRoll) -> dict:
    """Sum in-place (scheduled) rent on occupied, non-excluded units. Annualize * 12."""
    occ = [u for u in roll.occupied() if u.in_place_rent]
    monthly = round(sum(u.in_place_rent or 0 for u in occ), 2)
    return {
        "span_id": "calc:in_place_income",
        "occupied_with_rent": len(occ),
        "monthly": monthly,
        "annual_gpr_in_place": round(monthly * 12, 2),
        "note": "This is in-place residential/retail rent, not NOI (no opex on the roll).",
    }


def compute_occupancy(roll: CanonicalRentRoll) -> dict:
    rows = [u for u in roll.units if not u.excluded]
    occ = roll.occupied()
    vac = roll.vacant()
    n = len(rows) or 1
    return {
        "span_id": "calc:occupancy",
        "unit_count": len(rows),
        "occupied": len(occ),
        "vacant": len(vac),
        "other": len(rows) - len(occ) - len(vac),
        "occupancy_pct": round(100.0 * len(occ) / n, 2),
        "header_unit_count": roll.meta.unit_count_header,
    }


def compute_implied_cap(annual_income: float, ask_price: float) -> dict:
    """Implied cap = income / ask. Caller must pass in-place annual or a stated NOI."""
    if ask_price <= 0:
        raise ValueError("ask_price must be positive")
    return {
        "span_id": "calc:implied_cap",
        "ask_price": ask_price,
        "income_used": annual_income,
        "implied_cap_pct": round(100.0 * annual_income / ask_price, 3),
        "note": "If income is in-place rent * 12, this is a gross cap, not NOI cap.",
    }


def lookup_unit(roll: CanonicalRentRoll, unit_id: str) -> dict:
    for u in roll.units:
        if u.unit_id.strip() == unit_id.strip():
            return {"span_id": u.span_id, **u.model_dump()}
    return {"error": f"unit {unit_id} not found", "span_id": None}
