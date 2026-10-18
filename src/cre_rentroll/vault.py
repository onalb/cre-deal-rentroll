from __future__ import annotations

import json
from pathlib import Path

from cre_rentroll.ingest import ingest_csv
from cre_rentroll.schemas import CanonicalRentRoll

ROOT = Path(__file__).resolve().parents[2]
DEALS = ROOT / "data" / "deals"


def save_deal(roll: CanonicalRentRoll) -> Path:
    d = DEALS / roll.meta.deal_id
    d.mkdir(parents=True, exist_ok=True)
    path = d / "units.json"
    path.write_text(roll.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_deal(deal_id: str) -> CanonicalRentRoll:
    path = DEALS / deal_id / "units.json"
    if not path.exists():
        raise FileNotFoundError(f"No ingested deal '{deal_id}'. Run: cre-rentroll ingest <csv>")
    return CanonicalRentRoll.model_validate_json(path.read_text(encoding="utf-8"))


def list_deals() -> list[str]:
    if not DEALS.exists():
        return []
    return sorted(p.name for p in DEALS.iterdir() if (p / "units.json").exists())


def ingest_and_save(csv_path: str | Path) -> CanonicalRentRoll:
    roll = ingest_csv(csv_path)
    save_deal(roll)
    return roll


def search_units(roll: CanonicalRentRoll, query: str, limit: int = 8) -> list[dict]:
    q = query.lower()
    hits = []
    for u in roll.units:
        blob = " ".join(
            str(x) for x in (u.unit_id, u.plan, u.status, u.unit_use, u.span_id) if x
        ).lower()
        if q in blob or q in u.unit_id.lower():
            hits.append(u.model_dump())
        if len(hits) >= limit:
            break
    return hits
