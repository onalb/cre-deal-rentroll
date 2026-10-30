export function fillChipPrompt(prompt, vault) {
  const raw = String(prompt || "");
  // Occupancy-only chip: leave {{Property Name}} for the user to complete.
  if (raw.includes("{{Property Name}}")) {
    return raw;
  }
  const name = vault?.property_name || "this property";
  const asOf = vault?.as_of || "the roll as-of";
  return raw.replaceAll("{property_name}", name).replaceAll("{as_of}", asOf);
}

export const DEMO_CHIPS = [
  {
    id: "abstract",
    level: "easy",
    label: "Abstract the roll",
    prompt:
      "What's the unit count, occupancy, in-place monthly income, vacant count, average unit sqft, and any month-to-month on this rent roll?",
  },
  {
    id: "predict_occupancy_only",
    level: "predict-ok",
    label: "Predict occupancy",
    prompt: "What occupancy do you predict for {{Property Name}}",
  },
  {
    id: "multi-1",
    level: "medium",
    label: "Multi-1",
    prompt:
      "Where is this property? Can you put it on a map, and how many units are vacant, and what is occupancy?",
  },
  {
    id: "multi-2",
    level: "medium",
    label: "Multi-2",
    prompt:
      "What is in-place monthly income, and what would annualized NOI be if opex is 35% of EGI?",
  },
  {
    id: "multi-3",
    level: "medium",
    label: "Multi-3",
    prompt:
      "Put this property on a map, how many units are month-to-month, and what cap rate is implied at a $12M ask?",
  },
  {
    id: "locate",
    level: "medium",
    label: "Locate the property",
    prompt: "Where is this property? Can you put it on a map?",
  },
  {
    id: "underwrite",
    level: "medium",
    label: "Income vs ask",
    prompt: "What's the annualized in-place income?",
  },
  {
    id: "buy",
    level: "hard",
    label: "Should I buy?",
    prompt: "Should I buy this, and what is it worth?",
  },
  {
    id: "cap-rates",
    level: "hard",
    label: "Cap-rate news",
    prompt:
      "What are recent multifamily cap rates or sales in this property's metro?",
  },
  {
    id: "predict",
    level: "predict",
    label: "Predict occupancy (full)",
    prompt:
      "Over the next 12 months, how many units expire or are MTM, what income is at risk, and what occupancy do you predict for {property_name}? Do not request a rent roll file.",
  },
  {
    id: "weather",
    level: "oos",
    label: "Weekend weather",
    prompt: "What's the weather this weekend?",
  },
];