---
name: rr-predict
description: Forward occupancy via the live vacancy prediction agent on :8000
---
Use `ask_prediction_agent` when the last message is about **future** occupancy, lease expirations, MTM waterfall, rollover, loss-to-lease, or predicted income. Both Predict occupancy chips **steer** this tool (occupancy-only `predict_occupancy_only`, and the fuller `predict` chip); they do not force a call. For the occupancy-only chip, answer occupancy and do not expand into expirations, MTM, loss-to-lease, or predicted income unless occupancy itself.

Do **not** call it for current-book rent or occupancy (`query_units` / `compute_income`), floor-plan vacant probability (`ask_vacancy_agent`), geocode, weather, or unrelated chips.

Cite the prediction agent's reply, including when it says the building is not trained or asks which floor plan. Rentroll only calls vacancy MCP (`POST /mcp` tool `ask_vacancy` with `{question, session_id}`); the question names the vault property and says do not parse. It never posts `/api/chat`, never uploads a roll, and never trains. Training is only via the vacancy-agent UI on :8000 (file picker + Send rent roll). Do not invent tenant names.
