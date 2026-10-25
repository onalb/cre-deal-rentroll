---
name: rr-predict
description: Forward occupancy via the live vacancy prediction agent on :8000
---
Use `ask_prediction_agent` when the last message is about **future** occupancy, lease expirations, MTM waterfall, rollover, loss-to-lease, or predicted income. Both Predict occupancy chips **steer** this tool (occupancy-only `predict_occupancy_only`, and the fuller `predict` chip); they do not force a call. For the occupancy-only chip, answer occupancy and do not expand into expirations, MTM, loss-to-lease, or predicted income unless occupancy itself.

Do **not** call it for current-book rent or occupancy (`query_units` / `compute_income`), floor-plan vacant probability (`ask_vacancy_agent`), geocode, weather, or unrelated chips.

If `ask_prediction_agent` returns that the building is not trained (and lists trained properties), or asks which floor plan / group / renovated variant with a bullet list of plans and no probabilities, **quote that reply as the assistant message and STOP ReAct**. That clarifying question **is the complete answer for this user turn** — not occupancy % from the vault, and not a `query_units` substitute. Call `score_gcr` on that quoted follow-up. Do **not** call `ask_prediction_agent` again this turn with a guessed plan or property. Never pick a property or floor plan the user did not write in THIS user message. If they did not name a property, do not substitute a vault name or a name from the trained list. Forward their question; quote the predictor. Do not invent occupancy from the vault roll. Do not ask the user to parse a rent roll on Rentroll.

If the last assistant message was that clarifying question and THIS user message is a plan name, renovated yes/no, or another short variant answer, call `ask_prediction_agent` again with the user's text. The MCP `session_id` is reused per username. Do not dump every plan's rates on a building-only ask.

Rentroll only calls vacancy MCP (`POST /mcp` tool `ask_vacancy` with `{question, session_id}`); the first question is the user's text plus do not parse/train — do not invent a building. It never posts `/api/chat`, never uploads a roll, and never trains. Training is only via the vacancy-agent UI on :8000 (file picker + Send rent roll). Do not invent tenant names.
