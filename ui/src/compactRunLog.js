/** Interview/compact view: drop duplicate operator lines, never unique facts. */

function msg(row) {
  return String(row?.message || "").trim();
}

function step(row) {
  return String(row?.step || "").trim();
}

function foldWs(s) {
  return String(s || "")
    .replace(/\(LLM\)/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function norm(s) {
  return foldWs(s).toLowerCase();
}

function isTaskTool(row) {
  return step(row) === "tool" && /^(create_plan|update_plan_status|update_plan)\b/i.test(msg(row));
}

function isReflectionTool(row) {
  return step(row) === "tool" && /^reflection\b/i.test(msg(row));
}

function isScoreGcrTool(row) {
  return step(row) === "tool" && /^score_gcr\b/i.test(msg(row));
}

function stripDecor(s) {
  return norm(s)
    .replace(/^(create_plan|update_plan_status|update_plan|reflection|score_gcr)\s*(\(mcp\))?\s*/i, "")
    .replace(/^need_replan\s*/i, "")
    .replace(/^[-—–:→]+\s*/, "")
    .trim();
}

function coveredBy(text, covers) {
  const a = stripDecor(text);
  if (!a) return true;
  const blob = (covers || []).map((c) => stripDecor(c)).join(" \n ");
  if (!blob) return false;
  if (blob.includes(a)) return true;
  if (a.includes(blob) && blob.length > 0 && a.length - blob.length < 24) return true;
  return false;
}

function draftRole(text) {
  const t = String(text || "").trim();
  if (/^draft before eval\b/i.test(t)) return "beforeEval";
  if (/^agent draft\b/i.test(t)) return "intern";
  return "other";
}

function draftBody(text) {
  const t = String(text || "").trim();
  if (/is empty \(0 characters\)\.?\s*$/i.test(t)) return "";
  const m = t.match(/^(?:agent draft|draft before eval):\s*([\s\S]*)$/i);
  return (m ? m[1] : t).trim();
}

function parseChars(text) {
  const m = String(text || "").match(/finished\s+react\s*[—\-–]\s*(\d+)\s*characters/i);
  return m ? Number(m[1]) : null;
}

function isLlmStart(text) {
  return /harness starting react/i.test(text);
}

function isLlmFinish(text) {
  return /harness finished react/i.test(text);
}

function summaryHasTimeTokens(text) {
  const t = String(text || "");
  const tok =
    /in\s+\d+\s*tok\b/i.test(t) ||
    /in\/out unknown/i.test(t) ||
    /~\s*\d+\s*tokens/i.test(t);
  return tok && /\d+(?:\.\d+)?s\b/.test(t);
}

function summaryHasChars(text) {
  return /\d+\s*characters/i.test(String(text || ""));
}

function parseGcrBits(text) {
  const t = foldWs(text);
  const g = t.match(/\bG=(\S+)/i);
  const c = t.match(/\brequest-C=(\S+)/i) || t.match(/\bC=(\S+)/i);
  const r = t.match(/\bR=(\S+)/i);
  const judge = t.match(/\bjudge=(\S+)/i);
  const vault = t.match(/\bvault_calc=(\S+)/i);
  const arrows = [...t.matchAll(/→\s*(\S+)/g)];
  const gate = arrows.length ? arrows[arrows.length - 1][1].replace(/[.,;]+$/, "") : "";
  let reason = "";
  const dash = t.lastIndexOf(" — ");
  if (dash >= 0) reason = t.slice(dash + 3).trim();
  const failed = t.match(/failed\s+[^→]+/i);
  if (failed && !reason) reason = failed[0].trim();
  return {
    g: g ? g[1].replace(/[.,;]+$/, "") : "",
    c: c ? c[1].replace(/[.,;]+$/, "") : "",
    r: r ? r[1].replace(/[.,;]+$/, "") : "",
    judge: judge ? judge[1].replace(/[.,;]+$/, "") : "",
    vault: vault ? vault[1].replace(/[.,;]+$/, "") : "",
    gate,
    reason: norm(reason),
  };
}

function sameGate(a, b) {
  const map = (g) => {
    const x = String(g || "").toLowerCase();
    if (x === "show" || x === "ok") return "ok";
    return x;
  };
  return map(a) === map(b);
}

function evalDupOfScore(evalMsg, scoreMsg) {
  const e = parseGcrBits(evalMsg);
  const s = parseGcrBits(scoreMsg);
  if ((e.judge || "").toLowerCase() === "not_run") return false;
  const scoresSame = e.g === s.g && e.c === s.c && e.r === s.r;
  const reasonSame =
    !e.reason ||
    (Boolean(s.reason) && (s.reason === e.reason || s.reason.includes(e.reason) || e.reason.includes(s.reason)));
  const vaultSame = !e.vault || !s.vault || e.vault === s.vault;
  const judgeSame = !e.judge || !s.judge || e.judge === s.judge;
  return scoresSame && reasonSame && vaultSame && judgeSame && sameGate(e.gate, s.gate);
}

function prevMatching(rows, i, pred) {
  for (let j = i - 1; j >= 0; j--) {
    if (pred(rows[j], j)) return rows[j];
  }
  return null;
}

function isBoxPulse(row) {
  return /^agentcore_/.test(step(row));
}

function isDroppedEvalPulse(row) {
  return step(row) === "agentcore_eval" && /validationexception|\bno spans\b/i.test(msg(row));
}

function boxName(row) {
  return step(row).replace(/^agentcore_/, "");
}

function summarizeBoxCluster(cluster) {
  const ok = [];
  const skipped = [];
  const failed = [];
  for (const row of cluster) {
    const name = boxName(row);
    const text = msg(row);
    if (/^skipped\b/i.test(text)) skipped.push(name);
    else if (/^failed\b/i.test(text)) failed.push(name);
    else ok.push(name);
  }
  let message;
  if (failed.length) {
    message = `boxes failed (${failed.join(", ")})`;
    if (ok.length) message += `; ok ${ok.join(", ")}`;
    if (skipped.length) message += `; skipped ${skipped.join(", ")}`;
  } else if (ok.length) {
    message = `boxes OK (${ok.join(", ")})`;
    if (skipped.length) message += `; skipped ${skipped.join(", ")}`;
  } else {
    message = `boxes skipped (${skipped.join(", ")})`;
  }
  const last = cluster[cluster.length - 1] || {};
  return { ...last, step: "agentcore", message };
}

function foldBoxPulses(rows) {
  const out = [];
  for (let i = 0; i < rows.length; ) {
    if (!isBoxPulse(rows[i])) {
      out.push(rows[i]);
      i += 1;
      continue;
    }
    const cluster = [];
    while (i < rows.length && isBoxPulse(rows[i])) {
      if (!isDroppedEvalPulse(rows[i])) cluster.push(rows[i]);
      i += 1;
    }
    if (cluster.length) out.push(summarizeBoxCluster(cluster));
  }
  return out;
}

/**
 * Filter a full run-log event list for interview display/copy.
 * Does not mutate the input rows except when cloning summary to append character count.
 */
export function compactRunLog(rows) {
  if (!rows?.length) return [];

  rows = foldBoxPulses(rows);

  const summaryRow = [...rows].reverse().find((r) => step(r) === "summary");
  const summaryMsg = summaryRow ? msg(summaryRow) : "";
  const canDropLlmPair = Boolean(summaryRow) && summaryHasTimeTokens(summaryMsg);

  let lastChars = null;
  for (const row of rows) {
    if (step(row) === "llm" && isLlmFinish(msg(row))) {
      const n = parseChars(msg(row));
      if (n != null) lastChars = n;
    }
  }

  const out = [];
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const st = step(row);
    const text = msg(row);

    if (st === "skills") {
      out.push(row);
      continue;
    }

    if (st === "plan") {
      const prev = rows[i - 1];
      const statusChanged = /\[[^\]]*completed[^\]]*\]/i.test(text) || /\bcompleted:/i.test(text);
      if (isTaskTool(prev) && coveredBy(text, [msg(prev)]) && !statusChanged) continue;
      out.push(row);
      continue;
    }

    if (st === "reflect") {
      const tool = rows[i - 1] && isReflectionTool(rows[i - 1])
        ? rows[i - 1]
        : prevMatching(rows, i, isReflectionTool);
      if (tool && coveredBy(text, [msg(tool)])) continue;
      out.push(row);
      continue;
    }

    if (st === "replan") {
      const covers = [];
      const tool = prevMatching(rows, i, (r) => isReflectionTool(r) || isTaskTool(r) || step(r) === "tool");
      const refl = prevMatching(rows, i, (r) => step(r) === "reflect" || isReflectionTool(r));
      const err = prevMatching(rows, i, (r) => step(r) === "error");
      if (tool) covers.push(msg(tool));
      if (refl) covers.push(msg(refl));
      if (err) covers.push(msg(err));
      if (covers.length && coveredBy(text, covers)) continue;
      out.push(row);
      continue;
    }

    if (st === "draft") {
      // Compact never shows intern `agent draft:` — prefixes / (LLM) tags
      // must not keep a second draft beside `Draft before eval:`.
      if (draftRole(text) === "intern") continue;
      const body = draftBody(text);
      const alreadySame = out.some((r) => step(r) === "draft" && draftBody(msg(r)) === body);
      if (alreadySame) continue;
      out.push(row);
      continue;
    }

    if (st === "agentcore_eval") {
      if (/validationexception|\bno spans\b/i.test(text)) continue;
      out.push(row);
      continue;
    }

    if (st === "eval") {
      if (/judge=not_run/i.test(text)) {
        out.push(row);
        continue;
      }
      const score = prevMatching(rows, i, isScoreGcrTool);
      if (score && evalDupOfScore(text, msg(score))) continue;
      out.push(row);
      continue;
    }

    if (st === "llm") {
      if (isLlmStart(text)) continue;
      if (isLlmFinish(text)) {
        if (canDropLlmPair) continue;
        if (summaryRow && lastChars != null && !summaryHasChars(summaryMsg)) continue;
        const alreadyFinish = out.some((r) => step(r) === "llm" && isLlmFinish(msg(r)));
        if (alreadyFinish && parseChars(text) === lastChars) continue;
        out.push(row);
        continue;
      }
      out.push(row);
      continue;
    }

    out.push(row);
  }

  if (lastChars != null && canDropLlmPair && !summaryHasChars(summaryMsg)) {
    const idx = out.findIndex((r) => step(r) === "summary");
    if (idx >= 0) {
      const cur = msg(out[idx]);
      if (!summaryHasChars(cur)) {
        out[idx] = { ...out[idx], message: `${cur} · ${lastChars} characters` };
      }
    }
  } else if (lastChars != null && summaryRow && !summaryHasTimeTokens(summaryMsg) && !summaryHasChars(summaryMsg)) {
    const idx = out.findIndex((r) => step(r) === "summary");
    if (idx >= 0) {
      out[idx] = { ...out[idx], message: `${msg(out[idx])} · ${lastChars} characters` };
    }
  }

  return out;
}
