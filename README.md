# CRE Deal Rentroll

Web app for commercial / multifamily **rent-roll Q&A**. Attach a CSV, ask questions. The model must **retrieve** from the vault, **calculate** with tools, and **look up** maps/news externally.

Not just tool calling: **scope → inventory → question**. Eval scores **groundedness, completeness, relevance**.

## Public URL (AWS)

Live front door: **https://umvtmdweif.us-east-1.awsapprunner.com**

That URL is **App Runner** (one container: nginx UI + FastAPI). It is the public chat URL, not the AgentCore Runtime host. ReAct runs on AgentCore Runtime `cre_deal_rentroll-Nh63z6GXmK` after App Runner calls `InvokeAgentRuntime`.

- **App Runner** — UI + FastAPI login/ingest. Chat turns call `InvokeAgentRuntime` when `AGENTCORE_RUNTIME_ARN` is set. Anyone else uses username only (no password).
- **AgentCore Runtime** — Strands `run_agent` ReAct at `/invocations` (`deploy/Dockerfile.agentcore`). Not the AgentCore Harness product as the chat loop.
- **DynamoDB** table `ml_interview` — login users and the rent-roll vault blob. Omit `DDB_ENDPOINT` so boto3 uses real AWS.
- **Lambda** — AgentCore Gateway target (`cre-deal-rentroll-gateway`) for `cre_ping`, vault `query_units` / `compute_*`, `osm_geocode`, and `web_search`. The deal agent calls the same `tools.TOOL_IMPLS` functions in-process. Do not put FastAPI + Strands on Lambda.

Deploy: `deploy/Dockerfile` + `deploy/cloudformation.yaml`. GitHub Action: `.github/workflows/deploy.yml` (`workflow_dispatch`). Repo secrets: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `OPENAI_API_KEY`.

## Map check (OpenStreetMap is free, no API key)

| Deal | OSM / web |
|---|---|
| **La Cordoba** (164 units) | **Found.** 12630 Ashford Point Drive, Houston, TX 77082. Nominatim: 29.7195, -95.6084 (Alief). Floor plans in the CSV match public listings. |
| **Bay View Gardens** (250 units) | **Found.** 9 Seminole Drive, Portsmouth, VA 23702. Nominatim display_name includes *Bayview Gardens*. SEM- unit prefixes match Seminole. |
| **Chambers Hill Elementary School** (252 units) | **Not found as a 252-unit apartment.** Public hits are a K-5 school (~260 students, 6450 Chambers Hill Rd, Harrisburg) or a **5-unit** building at 6000 Chambers Hill Rd. Do not geocode the school as this asset. |

## Ingest

Two parsers:

- **La Cordoba / RealPage:** parent unit + `Charge Total:` child lines. Names are **not** stored.
- **Bay View / Chambers Hill:** one row per `UnitID` (`InPlaceRent`, occupancy).

```text
pip install -e .
cre-rentroll ingest "C:\Users\burak\Desktop\ML Interview\La Cordoba Rent Roll 04-20-21 (2) 164.csv"
cre-rentroll ingest "C:\Users\burak\Desktop\ML Interview\RentRoll-Bay View Gardens Apartments-20210930-040414 - 250.csv"
cre-rentroll ingest "C:\Users\burak\Desktop\ML Interview\RentRoll-Chambers Hill Elementary School-20231024-180834 - 252.csv"
cre-rentroll deals
```

## Eval without gold (production)

- **Groundedness:** every number/location needs a `span_id` from vault/calc/OSM/URL.
- **Completeness:** required tools for that question ran.
- **Relevance:** forbidden tools did not run.

A golden NOI file is optional for CI. Microsoft’s `CompletenessEvaluator` is the one that wants GroundTruth; G and R do not.

## Stacks

Strands is the agent **framework** (chat loop stays `run_agent` on Runtime). **App Runner** is the public chat URL. **AgentCore Runtime** hosts `/invocations` + `/ping`. Other diagram boxes are real AgentCore APIs on that invoke path (see mapping below). Dynamo `ml_interview` still holds login users and the rent-roll vault blob.

| Diagram box | AWS resource / API | Status |
|---|---|---|
| Runtime | `cre_deal_rentroll-Nh63z6GXmK` (`InvokeAgentRuntime`) | live ReAct host (not the public URL) |
| Harness / CLI / SDK | Harness `cre_deal_rentroll-O9vmgUgTse` + Runtime HTTP `/invocations` `/ping` | live |
| Built-in tools | Code Interpreter `cre_deal_rentroll_ci-gn1EOecInj` + Browser `cre_deal_rentroll_browser-Ic5Tw7nTd6` | live (session pulse each turn) |
| Gateway | MCP Gateway `credealrentroll-25yvwacjrd` + Lambda `cre-deal-rentroll-gateway` | live (vault/geo/search; deal agent calls `tools.TOOL_IMPLS`) |
| Identity | Runtime header WAT when present; else mint user-created `cre_deal_rentroll_wli` (not service-linked runtime id) | live |
| Memory | `CreDealRentrollMem-l47sZRGE6Y` STM events + semantic LTM; Dynamo still users + vault | live |
| Policy | Engine `cre_deal_rentroll_policy-bcwtc8qmov` Cedar `allow_cre_tools` LOG_ONLY on Gateway | live |
| Payments | unused | skipped (no microtransactions) |
| Registry | `CreateRegistry` denied for this account root | skipped; skills from `GET {CAPABILITIES_GATEWAY_URL}/skills/{name}` |
| Observability | ADOT/`opentelemetry-instrument` + dashboard `cre-deal-rentroll-agentcore` | live after runtime image |
| Evaluations | **Batch** over Runtime CloudWatch traces (`deploy/start_batch_evaluation.py`). Live G/C/R is still `score_gcr`. No per-turn `client.evaluate`. | batch |

Resident names on La Cordoba are stripped at ingest and should not be committed.

## Capability tools

`query_units`, `compute_income`, `compute_noi`, `compute_cap_rate`, `osm_geocode`, `web_search`, `ask_vacancy_agent`, and `ask_prediction_agent` POST `{CAPABILITIES_GATEWAY_URL}/mcp/deal`. The deal process does not run that math. `ask_prediction_agent` is forwarded by the gateway to the vacancy app's `ask_vacancy` MCP tool. `ask_vacancy_agent` scores floor plans on the vault roll inside the gateway. `create_plan`, `update_plan`, `update_plan_status`, and `reflection` also POST `{CAPABILITIES_GATEWAY_URL}/mcp/deal`. This process holds the checklist and writes back the rows the gateway returns. `score_gcr` is a separate Lambda this process invokes (`SCORE_GCR_LAMBDA_ARN`, handler `cre_rentroll.score_gcr_lambda.handler`, which calls `live_judge`). When that variable is unset, the handler runs in this process. It is not sent through the capabilities gateway. Username, as-of, search-fail, and the question stay here; the handler receives the draft, the query, the roll snapshot, and traces. Skill markdown is not in this repo. Each in-scope turn fetches `GET {CAPABILITIES_GATEWAY_URL}/skills/{name}` for `rent-roll-abstract`, `citation-policy`, and `underwriting`, and also `rr-predict` when that turn allows prediction. If `CAPABILITIES_GATEWAY_URL` is unset, those tools return an error that the capabilities gateway is required, and skill load fails the same way.
