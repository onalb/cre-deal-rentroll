# Ingest

Attach CSV/xlsx → decode + strip PII → detect layout → parse RealPage or one-row → `CanonicalRentRoll` → Dynamo vault + `FACT#`.

Column mapping is header/regex, not an LLM extract. Income is **Scheduled Charges / Charge Total / Rent Charge / Amount**, never a Lease End date. Occupied vs vacant comes from Unit Status / `vacant`, not “lease ended before as-of.”

Layout on a live extract is `realpage` or `one_row`. An unused LLM extract helper remains in `ingest.py` behind a dead flag (`CRE_LLM_INGEST = False`); `ingest_bytes` never calls OpenAI.

Ack in chat: saved property, unit count, as-of, geo_confidence; tenant names were removed.
