---
name: rr-predict
description: Forward occupancy via the live vacancy prediction agent on :8000
---
Use `ask_prediction_agent` when the last message is about **future** occupancy, lease expirations, MTM waterfall, rollover, loss-to-lease, or predicted income. The Predict occupancy chip **steers** this tool; it does not force a call.

Do **not** call it for current-book rent or occupancy (`query_units` / `compute_income`), floor-plan vacant probability (`ask_vacancy_agent`), geocode, weather, or unrelated chips.

Cite the prediction agent's reply, including when it says the building is not trained. Rentroll only calls vacancy MCP (`POST /mcp` tool `ask_vacancy` with `{question, session_id}`); it never posts `/api/chat`, never uploads a roll, and never trains. Training is only via the vacancy-agent UI on :8000 (file picker + Send rent roll). Do not invent tenant names.
