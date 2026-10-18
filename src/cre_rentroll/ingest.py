from __future__ import annotations

import csv
import re
from pathlib import Path

from cre_rentroll.schemas import CanonicalRentRoll, PropertyMeta, UnitRow, parse_money, parse_sqft

# Public addresses we could confirm (unit count / floor plans match the files).
KNOWN_ADDRESSES: dict[str, tuple[str, str]] = {
    "la cordoba": (
        "12630 Ashford Point Drive, Houston, TX 77082",
        "164 units + floor plans 0x1 A / 2x2 G 947sf match apartments.com",
    ),
    "bay view gardens": (
        "9 Seminole Drive, Portsmouth, VA 23702",
        "250 units; OSM display_name includes Bayview Gardens; SEM- building codes",
    ),
}


def ingest_csv(path: str | Path) -> CanonicalRentRoll:
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    sample = text[:2500].lower()
    if "bldg-unit" in sample or "charge total" in sample:
        return _parse_lacordoba(path, text)
    return _parse_unit_row(path, text)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:60] or "deal"


def _known_address(name: str) -> tuple[str | None, str, str | None]:
    key = name.lower()
    for needle, (addr, note) in KNOWN_ADDRESSES.items():
        if needle in key:
            return addr, "high", note
    return None, "none", (
        "No street on the rent roll; web search did not find a 252-unit match "
        "(Chambers Hill Elementary is a school, ~260 students, not this asset)."
        if "chambers" in key
        else "No street on the rent roll; geocode needs web_search or user city."
    )


def _parse_lacordoba(path: Path, text: str) -> CanonicalRentRoll:
    rows = list(csv.reader(text.splitlines()))
    name = "La Cordoba"
    as_of = None
    for row in rows[:8]:
        cells = [c.strip() for c in row if c.strip()]
        if not cells:
            continue
        if cells[0].lower() not in {"rent roll", "unit details"} and "cordoba" in cells[0].lower():
            name = cells[0]
        if re.search(r"20\d{2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec", cells[0], re.I):
            as_of = cells[0]
    addr, conf, note = _known_address(name)
    units: list[UnitRow] = []
    current: dict | None = None
    header_seen = False
    idx = 0
    for row in rows:
        if row and row[0].strip() == "Bldg-Unit":
            header_seen = True
            continue
        if not header_seen:
            continue
        if row and row[0].strip() in {"Summary", "Average Charges by Unit Type Summary", "Future Resident Details"}:
            break
        uid = (row[0] or "").strip() if row else ""
        if uid and re.match(r"^\d", uid):
            if current:
                units.append(_finish_parent(current, idx))
                idx += 1
            status = (row[3] or "").strip()
            excluded = "excluded" in status.lower() or "model" in status.lower()
            current = {
                "unit_id": uid,
                "plan": (row[1] or "").strip() or None,
                "sqft": parse_sqft(row[2] if len(row) > 2 else None),
                "status": status or "unknown",
                "market_rent": parse_money(row[5] if len(row) > 5 else None),
                "in_place_rent": None,
                "lease_start": (row[11] or "").strip() or None if len(row) > 11 else None,
                "lease_end": (row[12] or "").strip() or None if len(row) > 12 else None,
                "excluded": excluded,
                "mtm": False,
            }
            # Sometimes rent is on the parent Charge Code column
            code = (row[6] or "").strip() if len(row) > 6 else ""
            amt = parse_money(row[7] if len(row) > 7 else None)
            if code.lower() == "rent charge" and amt is not None:
                current["in_place_rent"] = amt
            continue
        if current and row and (row[6] or "").strip():
            code = row[6].strip()
            amt = parse_money(row[7] if len(row) > 7 else None)
            if code.lower().startswith("charge total") and amt is not None:
                current["in_place_rent"] = amt
            if "month-to-month" in code.lower():
                current["mtm"] = True
    if current:
        units.append(_finish_parent(current, idx))
    meta = PropertyMeta(
        deal_id=_slug(name),
        name=name,
        as_of=as_of,
        unit_count_header=164,
        source_file=str(path),
        layout="lacordoba",
        address=addr,
        address_confidence=conf,  # type: ignore[arg-type]
        address_note=note,
    )
    return CanonicalRentRoll(meta=meta, units=units)


def _finish_parent(d: dict, idx: int) -> UnitRow:
    uid = d["unit_id"]
    return UnitRow(
        unit_id=uid,
        plan=d.get("plan"),
        sqft=d.get("sqft"),
        status=d.get("status") or "unknown",
        market_rent=d.get("market_rent"),
        in_place_rent=d.get("in_place_rent"),
        lease_start=d.get("lease_start"),
        lease_end=d.get("lease_end"),
        mtm=bool(d.get("mtm")),
        excluded=bool(d.get("excluded")),
        span_id=f"unit:{uid}",
        unit_use="excluded" if d.get("excluded") else "residential",
    )


def _parse_unit_row(path: Path, text: str) -> CanonicalRentRoll:
    rows = list(csv.reader(text.splitlines()))
    name = path.stem
    as_of = None
    header_n = None
    for row in rows[:8]:
        joined = " ".join(c.strip() for c in row if c.strip())
        if "units" in joined.lower() and "(" in joined:
            name = joined.split("(")[0].strip(" ,")
            m = re.search(r"(\d+)\s*units", joined, re.I)
            if m:
                header_n = int(m.group(1))
        if "as of" in joined.lower():
            as_of = joined.split("as of:")[-1].strip(" ,")
    header_idx = None
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row]
        if "UnitID" in cells or (len(cells) > 1 and cells[1] == "UnitID"):
            header_idx = i
            headers = cells
            break
    if header_idx is None:
        raise ValueError(f"No UnitID header in {path}")

    def col(*names: str) -> int | None:
        for n in names:
            if n in headers:
                return headers.index(n)
        return None

    i_unit = col("UnitID")
    i_plan = col("PlanID")
    i_sf = col("NetSF")
    i_bed = col("Bed")
    i_bath = col("Bath")
    i_occ = col("OccStatus")
    i_mkt = col("MktRent")
    i_ipr = col("InPlaceRent")
    i_start = col("LeaseStart")
    i_end = col("LeaseExp")
    i_mtm = col("mtm")
    i_use = col("UnitType")
    units: list[UnitRow] = []
    for row in rows[header_idx + 1 :]:
        if len(row) <= (i_unit or 0):
            continue
        uid = (row[i_unit] or "").strip() if i_unit is not None else ""
        if not uid:
            continue
        mtm_val = (row[i_mtm] or "").strip() if i_mtm is not None and i_mtm < len(row) else ""
        use = (row[i_use] or "").strip() if i_use is not None and i_use < len(row) else None
        units.append(
            UnitRow(
                unit_id=uid,
                plan=(row[i_plan] or "").strip() if i_plan is not None and i_plan < len(row) else None,
                sqft=parse_sqft(row[i_sf] if i_sf is not None and i_sf < len(row) else None),
                beds=(row[i_bed] or "").strip() if i_bed is not None and i_bed < len(row) else None,
                baths=(row[i_bath] or "").strip() if i_bath is not None and i_bath < len(row) else None,
                status=(row[i_occ] or "").strip() if i_occ is not None and i_occ < len(row) else "unknown",
                unit_use=use or "residential",
                market_rent=parse_money(row[i_mkt] if i_mkt is not None and i_mkt < len(row) else None),
                in_place_rent=parse_money(row[i_ipr] if i_ipr is not None and i_ipr < len(row) else None),
                lease_start=(row[i_start] or "").strip() or None if i_start is not None and i_start < len(row) else None,
                lease_end=(row[i_end] or "").strip() or None if i_end is not None and i_end < len(row) else None,
                mtm=mtm_val.upper() == "MTM" or mtm_val == "-",
                span_id=f"unit:{uid}",
            )
        )
    addr, conf, note = _known_address(name)
    meta = PropertyMeta(
        deal_id=_slug(name),
        name=name,
        as_of=as_of,
        unit_count_header=header_n,
        source_file=str(path),
        layout="unit_row",
        address=addr,
        address_confidence=conf,  # type: ignore[arg-type]
        address_note=note,
    )
    return CanonicalRentRoll(meta=meta, units=units)
