function scoreLine(letter, value, why) {
  if (value == null || value === "") return "";
  const reason = String(why || "").trim();
  return reason ? `${letter} ${value}. ${reason}` : `${letter} ${value}.`;
}

function accuracyLine(row) {
  const facts = Array.isArray(row?.facts) ? row.facts : [];
  if (row?.accuracy == null && !facts.length) return "";
  const head = row.accuracy === true ? "A pass." : row.accuracy === false ? "A miss." : "A was not scored.";
  const parts = facts.map((fact) => {
    const value = fact.expected ? ` ${fact.expected}` : "";
    const verb = fact.hit ? "is in the answer" : "is not in the answer";
    return `${fact.label}${value} ${verb}.`;
  });
  return [head, ...parts].filter(Boolean).join(" ");
}

function namedScores(row) {
  const good = [];
  const bad = [];
  for (const [label, value] of [
    ["Groundedness", row.G],
    ["Completeness", row.C],
    ["Relevance", row.R],
  ]) {
    if (value == null) continue;
    if (value >= 4) good.push(`${label} ${value}`);
    else bad.push(`${label} ${value}`);
  }
  return { good, bad };
}

export function runBrief(row) {
  const well = [];
  const wrong = [];
  if (row?.error) wrong.push(String(row.error));

  const { good, bad } = namedScores(row || {});
  if (good.length) well.push(`${good.join(", ")}.`);
  if (bad.length) wrong.push(`${bad.join(", ")}.`);
  if (row?.reason && bad.length) wrong.push(String(row.reason));

  const facts = Array.isArray(row?.facts) ? row.facts : [];
  const hits = facts.filter((fact) => fact.hit).map((fact) => fact.label);
  const misses = facts.filter((fact) => !fact.hit).map((fact) => fact.label);
  if (row?.accuracy === true) {
    well.push("Accuracy passed. The answer matched the gold answer.");
  } else if (hits.length) {
    well.push(`Matched ${hits.join(", ")}.`);
  }
  if (misses.length) wrong.push(`Missing from the gold answer: ${misses.join(", ")}.`);

  const calls = row?.tool_calls;
  if (calls && calls.budget != null) {
    if (calls.ok) {
      well.push(`Tool calls matched the budget of ${calls.budget}.`);
    } else {
      const bits = [`Used ${calls.used} tool calls against a budget of ${calls.budget}.`];
      if (calls.extra?.length) bits.push(`Extra: ${calls.extra.join(", ")}.`);
      if (calls.missing?.length) bits.push(`Missing tools: ${calls.missing.join(", ")}.`);
      wrong.push(bits.join(" "));
    }
  }

  const scores = [
    scoreLine("G", row?.G, row?.g_reason),
    scoreLine("C", row?.C, row?.c_reason),
    scoreLine("R", row?.R, row?.r_reason),
    accuracyLine(row),
  ].filter(Boolean);
  const said = `${well.join(" ")} ${wrong.join(" ")}`;
  const hasAxis = Boolean(row?.g_reason || row?.c_reason || row?.r_reason);
  if (!hasAxis && row?.reason && !said.includes(String(row.reason))) {
    scores.push(`Judge. ${row.reason}`);
  }

  return {
    well: well.join(" ") || "Nothing in this run cleared the checks.",
    wrong: wrong.join(" ") || "Nothing in this run failed the checks.",
    scores,
  };
}
