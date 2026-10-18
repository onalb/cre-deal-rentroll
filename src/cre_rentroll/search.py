from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "data" / "web" / "cap_rate_fixtures.json"

# Offline / demo snippets so Q3 still runs if DuckDuckGo is blocked.
DEFAULT_HITS = {
    "houston multifamily cap rate": [
        {
            "title": "Houston multifamily cap rates (demo fixture)",
            "url": "https://example.com/houston-mf-caps",
            "snippet": "Houston garden multifamily cap rates in the early 2020s often clustered around 5–6% depending on class and submarket (Alief/West Houston).",
        }
    ],
    "portsmouth va multifamily cap rate": [
        {
            "title": "Hampton Roads multifamily caps (demo fixture)",
            "url": "https://example.com/hampton-roads-mf-caps",
            "snippet": "Hampton Roads / Portsmouth workforce multifamily implied caps were commonly discussed in the mid-5% to 7% range in 2021 vintage deals.",
        }
    ],
}


def web_search(query: str, *, offline: bool = False) -> dict:
    """External retrieval. Live ddgs if installed; else fixtures. Never invent URLs."""
    q = query.lower()
    key = next((k for k in DEFAULT_HITS if k.split()[0] in q or k in q), None)
    if "houston" in q or "alief" in q:
        key = "houston multifamily cap rate"
    elif "portsmouth" in q or "hampton" in q or "bay view" in q:
        key = "portsmouth va multifamily cap rate"
    hits = DEFAULT_HITS.get(key or "", [])
    if offline or not hits:
        if hits:
            return {"span_id": "web:search", "query": query, "hits": hits, "source": "fixture"}
        return {"span_id": "web:search", "query": query, "hits": [], "source": "empty"}
    try:
        from ddgs import DDGS  # type: ignore

        live = []
        with DDGS() as ddg:
            for row in ddg.text(query, max_results=3):
                live.append(
                    {
                        "title": row.get("title"),
                        "url": row.get("href") or row.get("url"),
                        "snippet": row.get("body"),
                    }
                )
        if live:
            return {"span_id": "web:search", "query": query, "hits": live, "source": "ddgs"}
    except Exception as exc:  # noqa: BLE001
        return {
            "span_id": "web:search",
            "query": query,
            "hits": hits,
            "source": "fixture_after_error",
            "error": str(exc),
        }
    return {"span_id": "web:search", "query": query, "hits": hits, "source": "fixture"}
