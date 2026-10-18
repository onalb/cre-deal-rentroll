---
name: citation-policy
description: Cite vault spans, this-turn compute_* JSON, OSM, and search URLs. Never cite tenant names or the raw CSV.
---

# Citations

- Ground numbers in vault unit-grid totals or this-turn `compute_*` tool JSON.
- Occupied-only `in_place_monthly` is not 100% occupancy income. Ground a full-occupancy estimate in `compute_income` `estimated_full_occupancy_monthly` (or mean occupied rent × unit count from that JSON).
- Cite the rent-roll report date (as-of) when stating occupancy or income.
- Ground maps in OSM `display_name` / lat / lon.
- Ground market claims in `web_search` hrefs.
- Never put the full unit grid or PII in the answer or in TRACE previews.
- Forbidden: text-only `end_turn` on a CRE/rent-roll answer with no `score_gcr`. You MUST call `score_gcr(draft)` on the user-facing sentence before you stop. Do not require RollAbstract JSON.
