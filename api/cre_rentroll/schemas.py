from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

_VACANT = re.compile(r"vacant|empty|down|model", re.I)
_OCCUPIED = re.compile(r"occup", re.I)


def parse_day(raw: object | None) -> date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


class UnitRow(BaseModel):
    unit_id: str
    unit_type: str | None = None
    sqft: float | None = None
    occupancy: str | None = None
    in_place_rent: float = 0.0
    vacant: bool = False
    mtm: bool = False
    charge_codes: dict[str, float] = Field(default_factory=dict)
    lease_start: str | None = None
    lease_end: str | None = None

    def vacant_on(self, as_of: str | date | None) -> bool:
        """Report Unit Status first (rent-roll as-of); lease window only if status is blank."""
        occ = self.occupancy or ""
        if _VACANT.search(occ) and not _OCCUPIED.search(occ):
            return True
        if _OCCUPIED.search(occ):
            return False
        day = parse_day(as_of)
        start = parse_day(self.lease_start)
        end = parse_day(self.lease_end)
        if day and (start or end):
            if start and start > day:
                return True
            if end and end < day:
                return True
            if end and end >= day:
                return False
            if start and start <= day and end is None:
                return False
        return self.vacant


def mean_positive_sqft(units: list[UnitRow]) -> float | None:
    """Mean sqft of units with sqft > 0. None when the roll has no sizes."""
    vals = [float(u.sqft) for u in units if u.sqft is not None and float(u.sqft) > 0]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


class CanonicalRentRoll(BaseModel):
    property_name: str | None = None
    as_of: str | None = None
    suggested_address: str | None = None
    geo_confidence: Literal["high", "medium", "low"] = "medium"
    layout: Literal["realpage", "one_row"]
    units: list[UnitRow]
    source_filename: str | None = None

    @property
    def unit_count(self) -> int:
        return len(self.units)

    @property
    def avg_sqft(self) -> float | None:
        return mean_positive_sqft(self.units)

    @property
    def sqft_n(self) -> int:
        return sum(1 for u in self.units if u.sqft is not None and float(u.sqft) > 0)

    def vacant_count_on(self, as_of: str | date | None = None) -> int:
        return sum(1 for u in self.units if u.vacant_on(as_of or self.as_of))

    @property
    def vacant_count(self) -> int:
        return self.vacant_count_on(self.as_of)

    @property
    def occupied_count(self) -> int:
        return self.unit_count - self.vacant_count

    def occupied_count_on(self, as_of: str | date | None = None) -> int:
        return self.unit_count - self.vacant_count_on(as_of or self.as_of)

    @property
    def occupancy_pct(self) -> float:
        if not self.units:
            return 0.0
        return self.occupied_count / self.unit_count

    def occupancy_pct_on(self, as_of: str | date | None = None) -> float:
        if not self.units:
            return 0.0
        return self.occupied_count_on(as_of) / self.unit_count

    @property
    def in_place_monthly(self) -> float:
        return self.in_place_monthly_on(self.as_of)

    def in_place_monthly_on(self, as_of: str | date | None = None) -> float:
        return sum(u.in_place_rent for u in self.units if not u.vacant_on(as_of or self.as_of))

    @property
    def mtm_count(self) -> int:
        return sum(1 for u in self.units if u.mtm)

    def vault_spans(self) -> list[str]:
        spans = [
            str(self.unit_count),
            str(self.vacant_count),
            str(round(self.in_place_monthly, 2)),
            str(self.occupied_count),
            f"{self.occupancy_pct:.4f}",
        ]
        if self.avg_sqft is not None:
            spans.append(str(self.avg_sqft))
        if self.property_name:
            spans.append(self.property_name)
        if self.as_of:
            spans.append(str(self.as_of))
        return spans


class RollAbstract(BaseModel):
    property_name: str | None = None
    unit_count: int
    occupied_count: int
    vacant_count: int
    occupancy_pct: float
    in_place_monthly: float
    mtm_count: int = 0
    as_of: str | None = None
    citations: list[str] = Field(default_factory=list)


class GeoFix(BaseModel):
    display_name: str | None = None
    lat: float | None = None
    lon: float | None = None
    geo_confidence: str = "medium"
    used_search: bool = False
    citations: list[str] = Field(default_factory=list)


class UnderwriteResult(BaseModel):
    in_place_annual: float | None = None
    noi: float | None = None
    noi_incomplete: bool = True
    implied_cap: float | None = None
    ask_price: float | None = None
    market_notes: str | None = None
    citations: list[str] = Field(default_factory=list)


class EvalScores(BaseModel):
    R: int | None = None
    G: int | None = None
    request_C: int | None = None
    vault_calc_ok: bool | None = None
    vault_calc: str | None = None
    retry: bool = False
    judge: str | None = None
    reason: str | None = None


class ChatOutcome(BaseModel):
    kind: Literal["answer", "warning", "error", "out_of_scope", "hitl_pending", "hitl_denied", "ingest_ack"]
    text: str
    eval: EvalScores | None = None
    stop_reason: str | None = None
    tools_called: list[str] = Field(default_factory=list)
    pending_hitl: dict[str, Any] | None = None
    ingest: dict[str, Any] | None = None
