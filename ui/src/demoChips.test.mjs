import assert from "node:assert/strict";
import { DEMO_CHIPS } from "./demoChips.js";

const ids = DEMO_CHIPS.map((c) => c.id);
assert.deepEqual(ids, [
  "abstract",
  "predict",
  "multi-1",
  "multi-2",
  "multi-3",
  "locate",
  "create-memo",
  "underwrite",
  "buy",
  "search-down",
  "weather",
]);

const predict = DEMO_CHIPS.find((c) => c.id === "predict");
assert.equal(predict.label, "Predict occupancy");
assert.match(predict.prompt, /12 months/i);
assert.match(predict.prompt, /occupancy/i);
assert.equal(predict.searchFail, false);
