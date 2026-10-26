"""CLI helpers. Income, occupancy, and cap math run on the capabilities gateway."""

from __future__ import annotations

from cre_rentroll.schemas import CanonicalRentRoll

_GATEWAY = "capabilities gateway is required (set CAPABILITIES_GATEWAY_URL)"


def compute_in_place_income(roll: CanonicalRentRoll) -> dict:
    raise RuntimeError(_GATEWAY)


def compute_occupancy(roll: CanonicalRentRoll) -> dict:
    raise RuntimeError(_GATEWAY)


def compute_implied_cap(annual_income: float, ask_price: float) -> dict:
    raise RuntimeError(_GATEWAY)


def lookup_unit(roll: CanonicalRentRoll, unit_id: str) -> dict:
    for u in roll.units:
        if u.unit_id.strip() == unit_id.strip():
            return {"span_id": u.span_id, **u.model_dump()}
    return {"error": f"unit {unit_id} not found", "span_id": None}
