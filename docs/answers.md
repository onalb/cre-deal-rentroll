# Questions and answers

A CRE question loads the vault snapshot (filled by **deterministic ingest** on attach) and `FACT#`, then the inventory agent (Strands/OpenAI tools) writes **prose**. That draft is what the user sees. Ingest is not an LLM extract.

Eval judges the draft afterward; it does not compose the answer. `present.answer_prose` does not rewrite occupancy % vs counts.

- Calculators: vault tools / MCP (`query_units`, `compute_income`, `compute_noi`, `compute_cap_rate`).
- Geocode: only Locator, and only when the user asked to locate/map.
- Out of scope: canned string, no model, no tools.
- No API key: tests/offline stub that returns tool JSON — not a production chat template.
