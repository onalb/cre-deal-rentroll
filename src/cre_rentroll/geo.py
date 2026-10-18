from __future__ import annotations

import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "data" / "geo" / "nominatim_fixtures.json"

# Recorded OSM hits from Nominatim (no key). Chambers Hill has no 252-unit match.
FIXTURES = {
    "12630 Ashford Point Drive, Houston, TX 77082": {
        "lat": "29.7194947",
        "lon": "-95.6084025",
        "display_name": (
            "12630, Ashford Point Drive, Ashford Point, Alief, Houston, "
            "Harris County, Texas, 77082, United States"
        ),
        "source": "nominatim",
    },
    "9 Seminole Drive, Portsmouth, VA 23702": {
        "lat": "36.7945535",
        "lon": "-76.3312699",
        "display_name": (
            "9, Seminole Drive, Bayview Gardens, Cradock, Portsmouth, "
            "Virginia, 23702, United States"
        ),
        "source": "nominatim",
    },
}


def geocode(query: str, *, offline: bool = False, user_agent: str = "cre-deal-rentroll/0.1") -> dict:
    """OpenStreetMap Nominatim — free, no API key. Be polite (1 req/s, real User-Agent)."""
    q = query.strip()
    if q in FIXTURES:
        return {"span_id": "geo:osm", "query": q, **FIXTURES[q]}
    if offline:
        return {"span_id": "geo:osm", "query": q, "error": "offline and no fixture", "source": "none"}
    url = "https://nominatim.openstreetmap.org/search"
    try:
        r = httpx.get(
            url,
            params={"q": q, "format": "json", "limit": 1},
            headers={"User-Agent": user_agent},
            timeout=20.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        if q in FIXTURES:
            return {"span_id": "geo:osm", "query": q, **FIXTURES[q], "note": f"live failed: {exc}"}
        return {"span_id": "geo:osm", "query": q, "error": str(exc), "source": "error"}
    if not data:
        return {"span_id": "geo:osm", "query": q, "error": "zero OSM hits", "source": "nominatim"}
    hit = data[0]
    return {
        "span_id": "geo:osm",
        "query": q,
        "lat": hit.get("lat"),
        "lon": hit.get("lon"),
        "display_name": hit.get("display_name"),
        "source": "nominatim",
    }


def write_fixtures() -> None:
    FIXTURE.mkdir(parents=True, exist_ok=True)
    (FIXTURE).write_text(json.dumps(FIXTURES, indent=2), encoding="utf-8")
