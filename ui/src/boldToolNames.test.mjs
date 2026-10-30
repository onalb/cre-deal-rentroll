import assert from "node:assert/strict";
import { boldToolNameParts, logMessageParts } from "./boldToolNames.js";

{
  const parts = boldToolNameParts("query_units (MCP) → 164 units");
  assert.deepEqual(parts, [{ bold: true, text: "query_units" }, " (MCP) → 164 units"]);
}
{
  const parts = boldToolNameParts("create_plan then update_plan");
  assert.deepEqual(
    parts.filter((p) => typeof p === "object").map((p) => p.text),
    ["create_plan", "update_plan"],
  );
}
{
  assert.deepEqual(boldToolNameParts(""), []);
}
{
  const tool = logMessageParts({ step: "tool", message: "query_units (MCP) → 164 units" });
  assert.deepEqual(tool, [{ bold: true, text: "query_units" }, " (MCP) → 164 units"]);
}
{
  const pred = logMessageParts({
    step: "tool",
    message: "ask_prediction_agent (MCP) → Which floor plan?",
  });
  assert.deepEqual(pred, [
    { bold: true, text: "ask_prediction_agent" },
    " (MCP) → Which floor plan?",
  ]);
}
{
  const react = logMessageParts({
    step: "react",
    message: "ReAct turn 2 — query_units for 1. [in_progress] query_units",
  });
  assert.deepEqual(react, ["ReAct turn 2 — query_units for 1. [in_progress] query_units"]);
}
{
  const plan = logMessageParts({
    step: "plan",
    message: "create_plan — 1. [pending] query_units",
  });
  assert.deepEqual(plan, ["create_plan — 1. [pending] query_units"]);
}
{
  assert.deepEqual(logMessageParts({ step: "summary", message: "3 tools · query_units" }), [
    "3 tools · query_units",
  ]);
}
