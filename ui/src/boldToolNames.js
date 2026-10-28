/** Same names as api/cre_rentroll/run_events.py MCP_TOOLS (longest first). */
export const LOG_TOOL_NAMES = [
  "compute_cap_rate",
  "compute_income",
  "query_units",
  "osm_geocode",
  "web_search",
  "create_plan",
  "update_plan_status",
  "update_plan",
  "compute_noi",
  "reflection",
  "score_gcr",
];

const TOOL_NAME_RE = new RegExp(`\\b(${LOG_TOOL_NAMES.join("|")})\\b`, "g");

export function boldToolNameParts(message) {
  const s = String(message || "");
  if (!s) return [];
  const parts = [];
  let last = 0;
  TOOL_NAME_RE.lastIndex = 0;
  let m;
  while ((m = TOOL_NAME_RE.exec(s))) {
    if (m.index > last) parts.push(s.slice(last, m.index));
    parts.push({ bold: true, text: m[0] });
    last = m.index + m[0].length;
  }
  if (last < s.length) parts.push(s.slice(last));
  return parts;
}

/** Bold tool names only on `tool` rows — not react, plan, summary, etc. */
export function logMessageParts(row) {
  const s = String(row?.message || "");
  if (!s) return [];
  if (String(row?.step || "").trim().toLowerCase() !== "tool") {
    return [s];
  }
  return boldToolNameParts(s);
}
