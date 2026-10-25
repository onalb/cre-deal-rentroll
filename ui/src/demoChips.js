export const DEMO_CHIPS = [
  {
    id: "abstract",
    level: "easy",
    label: "Abstract the roll",
    searchFail: false,
    prompt:
      "What's the unit count, occupancy, in-place monthly income, vacant count, and any month-to-month on this rent roll?",
  },
  {
    id: "predict",
    level: "predict",
    label: "Predict occupancy",
    searchFail: false,
    prompt:
      "Over the next 12 months, how many units expire or are MTM, what income is at risk, and what occupancy do you predict?",
  },
  {
    id: "multi-1",
    level: "medium",
    label: "Multi-1",
    searchFail: false,
    prompt:
      "Where is this property? Can you put it on a map, and how many units are vacant, and what is occupancy?",
  },
  {
    id: "multi-2",
    level: "medium",
    label: "Multi-2",
    searchFail: false,
    prompt:
      "What is in-place monthly income, and what would annualized NOI be if opex is 35% of EGI?",
  },
  {
    id: "multi-3",
    level: "medium",
    label: "Multi-3",
    searchFail: false,
    prompt:
      "Put this property on a map, how many units are month-to-month, and what cap rate is implied at a $12M ask?",
  },
  {
    id: "locate",
    level: "medium",
    label: "Locate the property",
    searchFail: false,
    prompt: "Where is this property? Can you put it on a map?",
  },
  {
    id: "create-memo",
    level: "medium",
    label: "Create memo",
    searchFail: false,
    prompt: "Create a memo synthesizing this session.",
  },
  {
    id: "underwrite",
    level: "medium",
    label: "Income vs ask",
    searchFail: false,
    prompt: "What's the annualized in-place income?",
  },
  {
    id: "buy",
    level: "hard",
    label: "Should I buy?",
    searchFail: false,
    prompt: "Should I buy this, and what is it worth?",
  },
  {
    id: "search-down",
    level: "hard",
    label: "Search fails",
    searchFail: true,
    prompt:
      "What are recent multifamily cap rates or sales in this property's metro?",
  },
  {
    id: "weather",
    level: "oos",
    label: "Weekend weather",
    searchFail: false,
    prompt: "What's the weather this weekend?",
  },
];
