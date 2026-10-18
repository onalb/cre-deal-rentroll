from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


def parse_money(value: object) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s in {"-", "--", "n/a", "N/A"}:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace("sf", "").strip()
    if not s:
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


def parse_sqft(value: object) -> float | None:
    if value is None:
        return None
    s = str(value).lower().replace("sf", "").strip()
    return parse_money(s)


class UnitRow(BaseModel):
    unit_id: str
    plan: str | None = None
    sqft: float | None = None
    beds: str | None = None
    baths: str | None = None
    status: str = "unknown"
    unit_use: str | None = None  # residential / retail / excluded
    market_rent: float | None = None
    in_place_rent: float | None = None
    lease_start: str | None = None
    lease_end: str | None = None
    mtm: bool = False
    excluded: bool = False
    span_id: str = ""


class PropertyMeta(BaseModel):
    deal_id: str
    name: str
    as_of: str | None = None
    unit_count_header: int | None = None
    source_file: str
    layout: Literal["lacordoba", "unit_row"]
    address: str | None = None
    address_confidence: Literal["high", "low", "none"] = "none"
    address_note: str | None = None


class CanonicalRentRoll(BaseModel):
    meta: PropertyMeta
    units: list[UnitRow] = Field(default_factory=list)

    def occupied(self) -> list[UnitRow]:
        rows = [u for u in self.units if not u.excluded]
        return [u for u in rows if "occup" in u.status.lower() and "non-revenue" not in u.status.lower()]

    def vacant(self) -> list[UnitRow]:
        rows = [u for u in self.units if not u.excluded]
        return [u for u in rows if "vacant" in u.status.lower()]
