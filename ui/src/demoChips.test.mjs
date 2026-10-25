import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DEMO_CHIPS, fillChipPrompt } from "./demoChips.js";

const ids = DEMO_CHIPS.map((c) => c.id);
assert.deepEqual(ids, [
  "abstract",
  "predict_occupancy_only",
  "multi-1",
  "multi-2",
  "multi-3",
  "locate",
  "create-memo",
  "underwrite",
  "buy",
  "search-down",
  "predict",
  "weather",
]);

const abstract = DEMO_CHIPS.find((c) => c.id === "abstract");
assert.equal(abstract.label, "Abstract the roll");
assert.match(abstract.prompt, /unit count/i);
assert.match(abstract.prompt, /occupancy/i);
assert.match(abstract.prompt, /vacant/i);
assert.match(abstract.prompt, /in-place monthly income/i);
assert.match(abstract.prompt, /average unit sqft/i);
assert.match(abstract.prompt, /month-to-month/i);
assert.doesNotMatch(abstract.prompt, /short bullet/i);
assert.doesNotMatch(abstract.prompt, /not one paragraph/i);
assert.equal(abstract.searchFail, false);

const agentsPy = readFileSync(
  new URL("../../api/cre_rentroll/agents.py", import.meta.url),
  "utf8",
);
const abstractSkill = readFileSync(
  new URL("../../skills/rent-roll-abstract/SKILL.md", import.meta.url),
  "utf8",
);
const citationSkill = readFileSync(
  new URL("../../skills/citation-policy/SKILL.md", import.meta.url),
  "utf8",
);
assert.match(agentsPy, /short bullet per asked item/);
assert.match(abstractSkill, /short bullet per asked item/);
assert.match(citationSkill, /short bullet per asked item/);

const predict = DEMO_CHIPS.find((c) => c.id === "predict");
assert.equal(predict.label, "Predict occupancy (full)");
assert.equal(predict.level, "predict");
assert.match(predict.prompt, /12 months/i);
assert.match(predict.prompt, /occupancy/i);
assert.match(predict.prompt, /expire|MTM/i);
assert.match(predict.prompt, /income is at risk/i);
assert.match(predict.prompt, /\{property_name\}/);
assert.doesNotMatch(predict.prompt, /as of|\{as_of\}/i);
assert.match(predict.prompt, /Do not request a rent roll file/i);
assert.equal(predict.searchFail, false);

const predictOnly = DEMO_CHIPS.find((c) => c.id === "predict_occupancy_only");
assert.equal(predictOnly.label, "Predict occupancy");
assert.equal(predictOnly.level, "predict-ok");
assert.equal(
  predictOnly.prompt,
  "What occupancy do you predict for {property_name}?",
);
assert.doesNotMatch(predictOnly.prompt, /as of/i);
assert.doesNotMatch(predictOnly.prompt, /expir|MTM|income at risk|loss-to-lease/i);
assert.equal(
  fillChipPrompt(predictOnly.prompt, { property_name: "La Cordoba", as_of: "2024-06-01" }),
  "What occupancy do you predict for La Cordoba?",
);
assert.equal(predictOnly.searchFail, false);

assert.notEqual(predict.label, predictOnly.label);

const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
assert.match(css, /button\.chip-predict\s*\{\s*--chip:\s*#9a3b2f\s*;/);
assert.match(css, /button\.chip-predict-ok\s*\{\s*--chip:\s*#2f6b4f\s*;/);
assert.equal(`chip-${predict.level}`, "chip-predict");
assert.equal(`chip-${predictOnly.level}`, "chip-predict-ok");
