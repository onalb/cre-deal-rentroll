import { compactRunLog } from "./compactRunLog.js";
import assert from "node:assert/strict";

function rows(...pairs) {
  return pairs.map(([step, message]) => ({ step, message }));
}

function steps(out) {
  return out.map((r) => r.step);
}

function msgs(out) {
  return out.map((r) => r.message);
}

{
  const checklist = "1. [pending] query_units\n2. [pending] compute_income";
  const out = compactRunLog(
    rows(
      ["inventory", "full tool menu (book)"],
      ["skills", "attached rent-roll-abstract, citation-policy, underwriting"],
      ["react", "ReAct turn 1 — create_plan (MCP) — for 1. [pending] query_units (LLM)"],
      ["tool", `create_plan (MCP) — ${checklist} (LLM)`],
      ["plan", checklist],
      ["react", "ReAct turn 2 — query_units (MCP) — for 1. [pending] query_units (LLM)"],
      ["tool", "query_units (MCP) → 164 units, occupied 151, vacant 13, MTM 0"],
      ["llm", "Harness starting ReAct (LLM)"],
      ["llm", "Harness finished ReAct — 412 characters (LLM)"],
      ["draft", "agent draft: Occupancy is 92.07%."],
      ["draft", "Draft before eval: Occupancy is 92.07%."],
      ["tool", "score_gcr (MCP) → G=5 request-C=5 R=5 vault_calc=ok judge=llm → ok"],
      ["eval", "G=5 request-C=5 R=5 vault_calc=ok judge=llm → show (LLM)"],
      ["stop", "eval_ok"],
      ["summary", "2 LLM (agent×1, judge) · 2 MCP · 0 skills · 0 retry · in 1234 tok · out 567 tok · 1.5s · confidence high"],
    ),
  );
  assert.deepEqual(
    steps(out),
    ["inventory", "skills", "react", "tool", "react", "tool", "draft", "tool", "stop", "summary"],
  );
  assert.ok(msgs(out).some((m) => m.includes("for 1. [pending]")));
  const draftMsgs = msgs(out).filter((_, i) => out[i].step === "draft");
  assert.equal(draftMsgs.length, 1);
  assert.ok(draftMsgs[0].startsWith("Draft before eval:"));
  assert.ok(!draftMsgs.some((m) => /^agent draft\b/i.test(m)));
  assert.ok(out.find((r) => r.step === "summary").message.includes("412 characters"));
  assert.ok(out.some((r) => r.step === "skills" && r.message.startsWith("attached ")));
  assert.ok(!steps(out).includes("plan"));
  assert.ok(!steps(out).includes("eval"));
  assert.ok(!steps(out).includes("llm"));
}

{
  const analysis = "Need occupancy from query_units then compute_income.";
  const out = compactRunLog(
    rows(
      ["tool", `reflection (MCP) need_replan — ${analysis} (LLM)`],
      ["reflect", `reflection (MCP) need_replan — ${analysis} (LLM)`],
      [
        "replan",
        "need_replan — call update_plan with statuses on the same checklist (agent, not inventory)",
      ],
    ),
  );
  assert.deepEqual(steps(out), ["tool", "replan"]);
}

{
  const out = compactRunLog(
    rows(
      ["tool", "score_gcr (MCP) → G=N/A request-C=N/A R=N/A vault_calc=N/A judge=not_run → error"],
      ["eval", "G=N/A C=N/A R=N/A vault_calc=N/A judge=not_run → error"],
    ),
  );
  assert.deepEqual(steps(out), ["tool", "eval"]);
}

{
  const out = compactRunLog(
    rows(
      ["tool", "score_gcr (MCP) → G=1 request-C=2 R=5 vault_calc=ok judge=llm → need_replan — failed G=1, request-C=2"],
      ["eval", "G=1 request-C=2 R=5 vault_calc=ok judge=llm → error — failed G=1, request-C=2"],
    ),
  );
  assert.ok(steps(out).includes("eval"), "different gate must keep eval");
}

{
  const out = compactRunLog(
    rows(
      ["react", "ReAct turn 4 — compute_income (MCP) — for 2. [pending] Compute income. (LLM)"],
      ["tool", "compute_income (MCP) → monthly 80019, occupied 151, vacant 13, MTM 0"],
      ["plan", "unique checklist not on previous tool"],
    ),
  );
  assert.ok(steps(out).includes("plan"));
  assert.ok(out.some((r) => r.step === "tool" && r.message.includes("monthly 80019")));
}

{
  const out = compactRunLog(
    rows(
      ["llm", "Harness starting ReAct (LLM)"],
      ["llm", "Harness finished ReAct — 99 characters (LLM)"],
      ["draft", "agent draft: hello"],
    ),
  );
  assert.equal(out.filter((r) => r.step === "llm").length, 1);
  assert.ok(out.find((r) => r.step === "llm").message.includes("99 characters"));
}

{
  const out = compactRunLog(
    rows(
      ["draft", "agent draft: Occupancy is 92.07%."],
      ["draft", "Draft before eval: Occupancy is 92.07%."],
    ),
  );
  assert.deepEqual(msgs(out), ["Draft before eval: Occupancy is 92.07%."]);
}

{
  const out = compactRunLog(
    rows(
      ["draft", "agent draft: first take"],
      ["draft", "Draft before eval: Occupancy is 92.07%."],
    ),
  );
  assert.deepEqual(msgs(out), ["Draft before eval: Occupancy is 92.07%."]);
}

{
  const out = compactRunLog(rows(["draft", "agent draft: only intern"]));
  assert.deepEqual(msgs(out), []);
}

{
  const out = compactRunLog(
    rows(
      ["draft", "agent draft: Occupancy is 92.07%. (LLM)"],
      ["draft", "Draft before eval: Occupancy is 92.07%."],
    ),
  );
  assert.deepEqual(msgs(out), ["Draft before eval: Occupancy is 92.07%."]);
  assert.ok(!msgs(out).some((m) => /agent draft/i.test(m)));
}

console.log("compactRunLog tests ok");
