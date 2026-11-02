import assert from "node:assert/strict";
import { runBrief } from "./runBrief.js";

const clean = runBrief({
  G: 5,
  C: 5,
  R: 5,
  accuracy: true,
  facts: [{ label: "unit count", hit: true }],
  tool_calls: { ok: true, used: 2, budget: 2, extra: [], missing: [] },
});
assert.match(clean.well, /Groundedness 5/);
assert.match(clean.well, /Accuracy passed/);
assert.match(clean.well, /budget of 2/);
assert.equal(clean.wrong, "Nothing in this run failed the checks.");
assert.match(clean.scores.join(" "), /A pass/);
assert.match(clean.scores.join(" "), /unit count is in the answer/);

const miss = runBrief({
  G: 5,
  C: 4,
  R: 5,
  accuracy: false,
  facts: [
    { label: "vacant count", hit: true },
    { label: "occupancy", hit: true },
    { label: "place", hit: false },
  ],
  tool_calls: {
    ok: false,
    used: 3,
    budget: 2,
    extra: ["osm_geocode"],
    missing: [],
  },
});
assert.match(miss.well, /Matched vacant count, occupancy/);
assert.match(miss.wrong, /Missing from the gold answer: place/);
assert.match(miss.wrong, /Extra: osm_geocode/);

const failed = runBrief({
  error: "The model is busy right now.",
  G: 2,
  C: 2,
  R: 5,
  reason: "Invented a vacant count.",
  accuracy: false,
  facts: [],
  tool_calls: { ok: false, used: 0, budget: 2, extra: [], missing: ["query_units", "osm_geocode"] },
});
assert.match(failed.wrong, /The model is busy/);
assert.match(failed.wrong, /Groundedness 2, Completeness 2/);
assert.match(failed.wrong, /Invented a vacant count/);
assert.match(failed.well, /Relevance 5/);

const explained = runBrief({
  G: 5,
  C: 4,
  R: 5,
  g_reason: "The vacant count matches the tool result.",
  c_reason: "The street was left out.",
  r_reason: "The answer stays on the map and occupancy question.",
  accuracy: false,
  facts: [
    { label: "vacant count", expected: "1", hit: true },
    { label: "place", expected: "100 Congress Ave", hit: false },
  ],
});
const text = explained.scores.join(" ");
assert.match(text, /G 5\. The vacant count matches the tool result/);
assert.match(text, /C 4\. The street was left out/);
assert.match(text, /R 5\. The answer stays on the map and occupancy question/);
assert.match(text, /A miss/);
assert.match(text, /place 100 Congress Ave is not in the answer/);
assert.match(text, /vacant count 1 is in the answer/);
