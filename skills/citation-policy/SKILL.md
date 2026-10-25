---
name: citation-policy
description: Cite vault spans, this-turn compute_* JSON, OSM, and search URLs. Never cite tenant names or the raw CSV.
---

# Citations

- Ground numbers in vault unit-grid totals or this-turn `compute_*` tool JSON.
- Ground average / typical unit size in this-turn `query_units` `avg_sqft`. Do not hand-sum or hand-average the units grid. Do not say the rent roll does not specify square footage when `avg_sqft` is present.
- Occupied-only `in_place_monthly` is not 100% occupancy income. Ground a full-occupancy estimate in `compute_income` `estimated_full_occupancy_monthly` (or mean occupied rent × unit count from that JSON).
- Cite the rent-roll report date (as-of) when stating occupancy or income.
- If the last `ask_prediction_agent` result is a clarifying question (which group/plan/variant, untrained / list trained buildings, or need a named building), quoting that tool reply is the complete, grounded answer for this turn. Do not require occupancy_pct / in_place_monthly citations, and do not substitute `query_units` occupancy.
- Ground maps in OSM `display_name` / lat / lon.
- Ground market claims in `web_search` hrefs.
- Never put the full unit grid or PII in the answer or in TRACE previews.
- Forbidden: text-only `end_turn` on a CRE/rent-roll answer with no `score_gcr`. You MUST call `score_gcr(draft)` on the user-facing answer (one sentence, or a short bullet per asked item on a multi-part question — never one paragraph for several asked items, even if the user did not request bullets) before you stop. Do not require RollAbstract JSON.
