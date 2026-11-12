"""Vault math for the AgentCore runtime.

The live gateway Lambda calls these tools back into the gateway and AgentCore
turns that into "An internal error occurred." The roll is already on this
process, so these four tools are answered here. The local gateway still runs
them itself.
"""

from __future__ import annotations

import json
from typing import Any

from cre_rentroll.schemas import CanonicalRentRoll, mean_positive_sqft
from cre_rentroll.vault import load_roll

NEED_FILE_TEXT = (
    "Please attach a rent-roll CSV or Excel file first. "
    "Once it is saved, I can answer follow-up questions about it."
)
HEADLINE_COUNTS_NOTE = (
    "These headline counts are complete in this result (including avg_sqft); "
    "another query_units is only needed to LIST a filtered subset of unit rows, "
    "not to re-fetch counts."
)


def run_vault_tool(name: str, arguments: dict[str, Any]) -> str:
    username = str(arguments.get("username") or "")
    as_of = str(arguments.get("as_of") or "")
    if name == "query_units":
        return query_units(
            occupancy=arguments.get("occupancy"),
            vacant_only=bool(arguments.get("vacant_only") or False),
            username=username,
            as_of=as_of,
        )
    if name == "compute_income":
        return compute_income(
            vacant_fill_rent=arguments.get("vacant_fill_rent"),
            username=username,
            as_of=as_of,
        )
    if name == "compute_noi":
        return compute_noi(
            operating_expenses=arguments.get("operating_expenses"),
            username=username,
        )
    if name == "compute_cap_rate":
        return compute_cap_rate(
            ask_price=float(arguments.get("ask_price") or 0),
            noi=arguments.get("noi"),
            username=username,
        )
    return json.dumps({"ok": False, "error": f"unknown vault tool {name}"})


def _need_file(name: str) -> str:
    return json.dumps({"need_file": True, "ok": True, "message": NEED_FILE_TEXT, "tool": name})


def _roll(username: str) -> CanonicalRentRoll | None:
    return load_roll(username)


def _vacant_asking_rent(unit: Any) -> float:
    rent = float(getattr(unit, "in_place_rent", 0) or 0)
    if rent > 0:
        return rent
    extras = getattr(unit, "charge_codes", None) or {}
    if not isinstance(extras, dict):
        return 0.0
    for key, val in extras.items():
        lk = str(key).lower()
        if "market" not in lk and lk not in {"asking", "ask", "_market_rent"}:
            continue
        try:
            n = float(val or 0)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return 0.0


def query_units(
    occupancy: str | None = None,
    vacant_only: bool = False,
    username: str = "",
    as_of: str = "",
) -> str:
    roll = _roll(username)
    if roll is None:
        return _need_file("query_units")
    units = roll.units
    day = (as_of or "").strip() or roll.as_of
    if vacant_only:
        units = [u for u in units if u.vacant_on(day)]
    if occupancy and str(occupancy).strip().lower() not in {"all", ""}:
        units = [u for u in units if (u.occupancy or "").lower() == str(occupancy).lower()]
    unfiltered = (not occupancy or str(occupancy).strip().lower() in {"all", ""}) and not vacant_only
    payload: dict[str, Any] = {
        "property_name": roll.property_name,
        "as_of": day,
        "geo_confidence": roll.geo_confidence,
        "suggested_address": roll.suggested_address,
        "unit_count": roll.unit_count,
        "occupied_count": roll.occupied_count_on(day),
        "vacant_count": roll.vacant_count_on(day),
        "occupancy_pct": round(roll.occupancy_pct_on(day), 4) if roll.unit_count else 0.0,
        "mtm_count": roll.mtm_count,
        "avg_sqft": mean_positive_sqft(units),
        "sqft_n": sum(1 for u in units if u.sqft is not None and float(u.sqft) > 0),
        "units": [u.model_dump() for u in units[:400]],
    }
    if unfiltered:
        payload["note"] = HEADLINE_COUNTS_NOTE
    return json.dumps(payload)


def compute_income(
    vacant_fill_rent: float | None = None,
    username: str = "",
    as_of: str = "",
) -> str:
    roll = _roll(username)
    if roll is None:
        return _need_file("compute_income")
    day = (as_of or "").strip() or roll.as_of
    monthly = roll.in_place_monthly_on(day)
    occupied = roll.occupied_count_on(day)
    vacant = roll.vacant_count_on(day)
    occ_pct = round(occupied / roll.unit_count, 4) if roll.unit_count else 0.0
    mean_occ = round(monthly / occupied, 2) if occupied else None
    vacant_asking = round(
        sum(_vacant_asking_rent(u) for u in roll.units if u.vacant_on(day)),
        2,
    )
    if vacant_asking > 0:
        full_occ = round(monthly + vacant_asking, 2)
        full_occ_method = "in_place_monthly + vacant_asking_monthly"
    elif occupied and roll.unit_count:
        full_occ = round((monthly / occupied) * roll.unit_count, 2)
        full_occ_method = "mean_occupied_rent * unit_count"
    else:
        full_occ = None
        full_occ_method = None
    payload: dict[str, Any] = {
        "in_place_monthly": round(monthly, 2),
        "in_place_annual": round(monthly * 12, 2),
        "occupied_count": occupied,
        "vacant_count": vacant,
        "unit_count": roll.unit_count,
        "occupancy_pct": occ_pct,
        "mean_occupied_rent": mean_occ,
        "vacant_asking_monthly": vacant_asking,
        "estimated_full_occupancy_monthly": full_occ,
        "estimated_full_occupancy_method": full_occ_method,
        "note": (
            "in_place_monthly is occupied units only. For 100% occupancy / all "
            "units occupied / no-vacant income, use estimated_full_occupancy_monthly "
            "(not in_place_monthly)."
        ),
        "mtm_count": roll.mtm_count,
        "as_of": day,
        "property_name": roll.property_name,
    }
    if vacant_fill_rent is not None:
        fill = float(vacant_fill_rent)
        fill_m = round(vacant * fill, 2)
        payload["vacant_fill_rent"] = round(fill, 2)
        payload["vacant_fill_monthly"] = fill_m
        payload["vacant_fill_annual"] = round(fill_m * 12, 2)
        payload["scenario_full_occupancy_monthly"] = round(monthly + fill_m, 2)
        payload["scenario_full_occupancy_annual"] = round((monthly + fill_m) * 12, 2)
        payload["note"] = (
            payload["note"]
            + " User-stated vacant fill: cite scenario_full_occupancy_annual "
            "(in_place_annual + vacant_fill_annual), not vacant_fill_annual alone."
        )
    return json.dumps(payload)


def compute_noi(
    operating_expenses: float | None = None,
    username: str = "",
    as_of: str = "",
) -> str:
    del as_of
    roll = _roll(username)
    if roll is None:
        return _need_file("compute_noi")
    income = round(roll.in_place_monthly * 12, 2)
    incomplete = operating_expenses is None
    noi = None if incomplete else round(income - float(operating_expenses), 2)
    return json.dumps(
        {
            "in_place_annual": income,
            "operating_expenses": operating_expenses,
            "noi": noi,
            "noi_incomplete": incomplete,
        }
    )


def compute_cap_rate(
    ask_price: float,
    noi: float | None = None,
    username: str = "",
    as_of: str = "",
) -> str:
    del as_of
    roll = _roll(username)
    if roll is None:
        return _need_file("compute_cap_rate")
    if ask_price <= 0:
        return json.dumps({"ok": False, "error": "ask_price must be positive"})
    income = roll.in_place_monthly * 12
    basis = float(noi) if noi is not None else income
    return json.dumps(
        {
            "ask_price": ask_price,
            "basis": round(basis, 2),
            "implied_cap": round(basis / ask_price, 6),
            "implied_on_income": noi is None,
            "note": "NOI incomplete — cap uses in-place income" if noi is None else "cap uses NOI",
        }
    )
