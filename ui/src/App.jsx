import { useEffect, useRef, useState } from "react";
import { compactRunLog } from "./compactRunLog";
import { DEMO_CHIPS, fillChipPrompt } from "./demoChips";
import { logMessageParts } from "./boldToolNames";

const KEY = "cre-rentroll-username";
const FETCH_MS = 40_000;
const CHAT_MS = 120_000;

function errorMessage(err, data, status) {
  const detail = data?.detail ?? data?.message ?? data?.text;
  const fromBody = typeof detail === "string" ? detail : Array.isArray(detail) ? detail[0]?.msg : "";
  if (err?.name === "AbortError") {
    return "That took too long. Wait a minute and send again.";
  }
  if (status === 429 || /429|rate limit/i.test(String(fromBody || err?.message || ""))) {
    return "The model is busy right now. Wait a minute and send again.";
  }
  if (status === 504 || status === 502) {
    return "The request timed out. Wait a minute and send again.";
  }
  return fromBody || err?.message || "Something went wrong. Please try again.";
}

function displayContent(m) {
  let c = m?.content || "";
  if (m?.role === "user") {
    c = c.replace(/\n*\[eval retry:[\s\S]*$/i, "").trim();
    return c;
  }
  c = c.replace(/\n*—\s*Warning:\s*live eval[\s\S]*$/i, "").trim();
  c = c
    .split("\n")
    .filter((line) => {
      if (/^\s*Creating the Memo\b/i.test(line)) return false;
      if (/^\s*Now I will proceed\b/i.test(line)) return false;
      if (/^\s*\d+\.\s*(?:\[[^\]]+\]\s*)?Publish Memo\s*$/i.test(line)) return false;
      return true;
    })
    .join("\n")
    .trim();
  return c;
}

function isHitlPendingPayload(data) {
  if (!data) return false;
  if (data.kind === "hitl_pending" || data.pending_hitl) return true;
  const msgs = data.history || data.messages;
  if (Array.isArray(msgs) && msgs.length) {
    const last = msgs[msgs.length - 1];
    if (last?.kind === "hitl_pending") return true;
  }
  return false;
}

async function api(path, { method = "GET", username, body, headers, timeoutMs = FETCH_MS } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: {
        ...(username ? { "X-Username": username } : {}),
        ...headers,
      },
      body,
      signal: ctrl.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    throw new Error(errorMessage(err, {}, 0));
  }
  clearTimeout(timer);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(errorMessage(null, data, res.status));
    err.data = data;
    err.status = res.status;
    throw err;
  }
  return data;
}

async function streamChat({ username, body, onLog, timeoutMs = CHAT_MS }) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "X-Username": username,
        Accept: "text/event-stream",
      },
      body,
      signal: ctrl.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    throw new Error(errorMessage(err, {}, 0));
  }
  const ctype = res.headers.get("content-type") || "";
  if (!ctype.includes("event-stream")) {
    const data = await res.json().catch(() => ({}));
    clearTimeout(timer);
    if (Array.isArray(data.logs)) data.logs.forEach(onLog);
    if (!res.ok) {
      const err = new Error(errorMessage(null, data, res.status));
      err.data = data;
      err.status = res.status;
      throw err;
    }
    return data;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let donePayload = null;
  let errPayload = null;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const chunks = buf.split("\n\n");
      buf = chunks.pop() || "";
      for (const block of chunks) {
        let event = "message";
        const dataLines = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
        }
        if (!dataLines.length) continue;
        let data;
        try {
          data = JSON.parse(dataLines.join("\n"));
        } catch {
          continue;
        }
        if (event === "log") onLog(data);
        else if (event === "done") donePayload = data;
        else if (event === "error") errPayload = data;
      }
    }
  } finally {
    clearTimeout(timer);
  }
  if (errPayload) {
    const status = errPayload.status || 500;
    const err = new Error(errorMessage(null, { text: errPayload.message }, status));
    err.status = status;
    throw err;
  }
  if (!donePayload) {
    throw new Error("Run ended without a result. Send again.");
  }
  if (donePayload.stop_reason === "openai_rate_limited") {
    const err = new Error(errorMessage(null, donePayload, 429));
    err.data = donePayload;
    err.status = 429;
    throw err;
  }
  return donePayload;
}

function evalLogTone(row) {
  if ((row?.step || "") !== "eval") return "";
  const gate = String(row?.data?.gate || "").toLowerCase();
  if (gate === "error") return "eval-fail";
  if (gate === "warn") return "eval-warn";
  if (gate === "show" || gate === "ok") return "eval-ok";
  const msg = String(row?.message || "");
  if (/→\s*error\b/i.test(msg)) return "eval-fail";
  if (/→\s*warn/i.test(msg)) return "eval-warn";
  return "eval-ok";
}

function runLogStatus(runLog, busy) {
  if (busy && (!runLog || runLog.length === 0)) return "waiting for the first step…";
  if (!runLog || runLog.length === 0) return "idle";
  const last = runLog[runLog.length - 1];
  const step = last.step || "log";
  const msg = (last.message || "").trim();
  if (!busy && step === "summary") return msg;
  const line = msg ? `${step} · ${msg}` : step;
  return busy ? `running · ${line}` : line;
}

function formatRunLogText(runLog) {
  if (!runLog?.length) return "";
  return runLog
    .map((row) => {
      const step = String(row.step || "").trim();
      const msg = String(row.message || "").trim();
      if (step && msg) return `${step}  ${msg}`;
      return step || msg;
    })
    .join("\n");
}

function applySession(setSession, data) {
  if (!data?.history && !data?.vault && !data?.facts && !data?.memos && !data?.pending_hitl) return;
  setSession((s) => ({
    ...s,
    messages: data.history ?? s?.messages,
    pending_hitl: data.pending_hitl,
    facts: data.facts ?? s?.facts,
    vault: data.vault ?? s?.vault,
    memos: data.memos ?? s?.memos,
  }));
}

export default function App() {
  const [username, setUsername] = useState(() => localStorage.getItem(KEY) || "");
  const [draftUser, setDraftUser] = useState(username || "analyst");
  const [session, setSession] = useState(null);
  const [health, setHealth] = useState(null);
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [searchFail, setSearchFail] = useState(false);
  const [chipId, setChipId] = useState("");
  const [runLog, setRunLog] = useState([]);
  const [compactArmed, setCompactArmed] = useState(false);
  const [vaultOpen, setVaultOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [logExpanded, setLogExpanded] = useState(false);
  const [logCopied, setLogCopied] = useState(false);
  const [logCompact, setLogCompact] = useState(true);
  const copyTimer = useRef(null);
  const bottom = useRef(null);
  const logEnd = useRef(null);
  const fileInput = useRef(null);
  const visibleRunLog = compactArmed ? (logCompact ? compactRunLog(runLog) : runLog) : [];

  useEffect(() => {
    api("/api/health", { timeoutMs: 8_000 }).then(setHealth).catch(() => setHealth({ ok: false }));
  }, []);

  useEffect(() => {
    if (!username) return;
    setRunLog([]);
    setCompactArmed(false);
    api("/api/session", { username, timeoutMs: 8_000 })
      .then(setSession)
      .catch((e) => setError(e.message));
  }, [username]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [session, busy]);

  useEffect(() => {
    logEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [runLog]);

  useEffect(() => {
    if (session?.pending_hitl) setVaultOpen(true);
    const msgs = session?.messages;
    if (Array.isArray(msgs) && msgs[msgs.length - 1]?.kind === "hitl_pending") {
      setVaultOpen(true);
    }
  }, [session?.pending_hitl, session?.messages]);

  async function login(e) {
    e.preventDefault();
    setError("");
    try {
      const data = await api("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: draftUser.trim() }),
        timeoutMs: 12_000,
      });
      localStorage.setItem(KEY, data.username);
      setUsername(data.username);
    } catch (err) {
      setError(err.message);
    }
  }

  function logout() {
    localStorage.removeItem(KEY);
    setUsername("");
    setSession(null);
    setRunLog([]);
    setCompactArmed(false);
    setVaultOpen(false);
    setLogOpen(false);
    setLogExpanded(false);
    setLogCopied(false);
  }

  function collapseRunLog() {
    setLogExpanded(false);
    setLogOpen(false);
  }

  function toggleLogExpand(e) {
    e.stopPropagation();
    setLogExpanded((open) => {
      if (!open) setLogOpen(true);
      return !open;
    });
  }

  function toggleLogPanel() {
    if (logExpanded || logOpen) {
      collapseRunLog();
      return;
    }
    setLogOpen(true);
  }

  async function copyRunLog(e) {
    e.stopPropagation();
    if (!visibleRunLog.length) return;
    try {
      await navigator.clipboard.writeText(formatRunLogText(visibleRunLog));
      setLogCopied(true);
      if (copyTimer.current) clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setLogCopied(false), 1400);
    } catch {
      /* clipboard permission denied */
    }
  }

  function disarmCompactLog() {
    if (busy) return;
    setRunLog([]);
    setCompactArmed(false);
  }

  function fillChip(chip) {
    if (busy) return;
    disarmCompactLog();
    setText(fillChipPrompt(chip.prompt, session?.vault));
    setSearchFail(Boolean(chip.searchFail));
    setChipId(chip.id || "");
    setError("");
  }

  async function send(e) {
    e.preventDefault();
    if (busy) return;
    if (!text.trim() && !file) return;
    const outgoing = text;
    const outgoingFile = file;
    const failFlag = Boolean(searchFail);
    const outgoingChip = chipId;
    const hasQuestion = Boolean(outgoing.trim());
    const rawUser = outgoing.trim() || (outgoingFile ? `[attached ${outgoingFile.name}]` : "");
    const userContent = displayContent({ role: "user", content: rawUser });
    setBusy(true);
    setError("");
    setText("");
    setFile(null);
    setRunLog([]);
    setCompactArmed(hasQuestion);
    collapseRunLog();
    setVaultOpen(false);
    setSession((s) => ({
      ...(s || {}),
      messages: [...(s?.messages || []), { role: "user", content: userContent }],
    }));
    try {
      const fd = new FormData();
      fd.append("message", outgoing);
      fd.append("search_fail", failFlag ? "true" : "false");
      if (outgoingChip) fd.append("chip_id", outgoingChip);
      if (outgoingFile) fd.append("file", outgoingFile);
      const data = await streamChat({
        username,
        body: fd,
        onLog: (ev) => {
          if (!hasQuestion) return;
          setRunLog((rows) => [...rows, ev]);
        },
      });
      if (hasQuestion && Array.isArray(data.logs) && data.logs.length) setRunLog(data.logs);
      applySession(setSession, data);
      if (isHitlPendingPayload(data)) setVaultOpen(true);
      if (fileInput.current) fileInput.current.value = "";
      setFile(null);
    } catch (err) {
      if (hasQuestion && Array.isArray(err.data?.logs) && err.data.logs.length) setRunLog(err.data.logs);
      applySession(setSession, err.data);
      if (!err.data?.history) {
        setSession((s) => ({
          ...s,
          messages: (s?.messages || []).slice(0, -1),
        }));
      }
      setText((current) => (current.trim() ? current : outgoing));
      setFile((current) => current || outgoingFile);
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function resolveHitl(allow) {
    setBusy(true);
    try {
      const data = await api("/api/hitl", {
        method: "POST",
        username,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allow }),
      });
      setSession((s) => ({
        ...s,
        messages: data.history,
        pending_hitl: null,
        memos: data.memos ?? s?.memos,
      }));
    } catch (err) {
      applySession(setSession, err.data);
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!username) {
    return (
      <div className="shell login-shell">
        <div className="card login-card">
          <p className="eyebrow">CRE Deal Rentroll</p>
          <h1>Username only</h1>
          <p className="lede">
            No password, no register. First use creates <code>USER#you / PROFILE</code> in
            Dynamo <code>ml_interview</code>. Chat, LTM FACT#, and TRACE# follow that user.
          </p>
          <form onSubmit={login}>
            <input
              autoFocus
              placeholder="analyst"
              value={draftUser}
              onChange={(e) => setDraftUser(e.target.value)}
            />
            <button type="submit">Enter</button>
          </form>
          {error && <p className="err">{error}</p>}
          <p className="hint">
            API {health?.ok ? "up" : "…"} · OpenAI key{" "}
            {health?.openai_key_present ? "present" : "missing (ingest still works)"}
          </p>
        </div>
      </div>
    );
  }

  const msgs = session?.messages || [];
  const pending = session?.pending_hitl;
  const vaultExpanded = vaultOpen;

  return (
    <div className={`shell${logExpanded ? " log-fs" : ""}`}>
      <header>
        <div>
          <p className="eyebrow">CRE Deal Rentroll</p>
          <h1>Deal chat</h1>
        </div>
        <div className="who">
          <span>
            {username}
            {session?.vault ? ` · ${session.vault.property_name || "vaulted"} (${session.vault.unit_count} u)` : " · no roll yet"}
          </span>
          <button className="ghost" onClick={logout} type="button">
            Switch user
          </button>
        </div>
      </header>

      <main>
        <section className="transcript">
          {msgs.length === 0 && (
            <div className="empty">
              Attach a rent-roll spreadsheet, then ask about units, location, or income.
              Everyday chat, greetings, and weather are out of scope.
            </div>
          )}
          {msgs.map((m, i) => (
            <article key={i} className={`msg ${m.role} ${m.kind || ""}`}>
              <span className="role">{m.role}</span>
              <pre>{displayContent(m)}</pre>
            </article>
          ))}
          {busy && (
            <article className="msg assistant working" aria-live="polite" aria-busy="true">
              <span className="role">assistant</span>
              <p className="working-line">
                <span className="spinner" aria-hidden="true" />
                Assistant is working…
              </p>
            </article>
          )}
          <div ref={bottom} />
        </section>
        <aside className={`vault ${vaultExpanded ? "open" : "closed"}`}>
          <button
            type="button"
            className="vault-toggle"
            aria-expanded={vaultExpanded}
            aria-controls="vault-body"
            title={vaultExpanded ? "Close vault" : "Open vault"}
            onClick={() => setVaultOpen((v) => !v)}
          >
            <span className="vault-horn" aria-hidden="true" />
            <span className="vault-spine">Vault</span>
          </button>
          <div className="vault-body" id="vault-body" aria-hidden={!vaultExpanded}>
            <h2>Vault / LTM</h2>
            <dl>
              <dt>geo_confidence</dt>
              <dd>{session?.vault?.geo_confidence || "—"}</dd>
            </dl>
            <ul className="facts">
              {Object.entries(session?.facts || {}).map(([k, v]) => (
                <li key={k}>
                  <code>FACT#{k}</code> {String(v)}
                </li>
              ))}
              {!session?.facts || Object.keys(session.facts).length === 0 ? <li>No FACT# yet</li> : null}
            </ul>
            <h2>Memos</h2>
            <ul className="facts memos">
              {(session?.memos || []).map((m) => (
                <li key={m.ts || m.title}>
                  <code>MEMO#{m.ts}</code>
                  <strong> {m.title}</strong>
                  {m.body ? <pre className="memo-body">{m.body}</pre> : null}
                </li>
              ))}
              {!session?.memos || session.memos.length === 0 ? <li>No MEMO# yet</li> : null}
            </ul>
            {pending && (
              <div className="hitl">
                <h2>HITL · publish_memo</h2>
                <p>{pending.title}</p>
                <div className="row">
                  <button type="button" disabled={busy} onClick={() => resolveHitl(true)}>
                    Approve
                  </button>
                  <button type="button" className="ghost" disabled={busy} onClick={() => resolveHitl(false)}>
                    Deny
                  </button>
                </div>
              </div>
            )}
          </div>
        </aside>
      </main>

      <aside
        className={`runlog ${logOpen || logExpanded ? "open" : "closed"}${logExpanded ? " expanded" : ""}`}
        aria-live="polite"
      >
        <div className="runlog-bar">
          <button
            type="button"
            className="runlog-toggle"
            aria-expanded={logOpen || logExpanded}
            aria-controls="runlog-body"
            title={logOpen || logExpanded ? "Close run log" : "Open run log"}
            onClick={toggleLogPanel}
          >
            <span className="runlog-horn" aria-hidden="true" />
            <span className="runlog-title">Run log</span>
          </button>
          <button
            type="button"
            className="ghost runlog-compact"
            aria-pressed={logCompact}
            title={logCompact ? "Compact on — interview view, duplicates hidden" : "Compact off — full event stream"}
            aria-label={logCompact ? "Compact interview view on" : "Compact interview view off"}
            onClick={(e) => {
              e.stopPropagation();
              setLogCompact((v) => !v);
            }}
          >
            Compact
          </button>
          <button
            type="button"
            className="ghost runlog-copy"
            disabled={visibleRunLog.length === 0}
            title={logCopied ? "Copied" : "Copy run log"}
            aria-label={logCopied ? "Copied" : "Copy run log"}
            onClick={copyRunLog}
          >
            {logCopied ? (
              <span className="runlog-copied">Copied</span>
            ) : (
              <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                <rect x="5.5" y="1.5" width="9" height="11" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.25" />
                <path d="M3.5 4.5H2.7A1.2 1.2 0 0 0 1.5 5.7v8.1A1.2 1.2 0 0 0 2.7 15h8.1a1.2 1.2 0 0 0 1.2-1.2v-.8" fill="none" stroke="currentColor" strokeWidth="1.25" />
              </svg>
            )}
          </button>
          <button
            type="button"
            className="ghost runlog-expand"
            title={logExpanded ? "Exit full screen" : "Expand run log"}
            aria-label={logExpanded ? "Exit full screen" : "Expand run log"}
            aria-pressed={logExpanded}
            onClick={toggleLogExpand}
          >
            {logExpanded ? (
              <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                <path d="M4 4l8 8M12 4L4 12" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" />
              </svg>
            ) : (
              <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                <path d="M5.5 2.5H2.5v3M10.5 2.5h3v3M5.5 13.5H2.5v-3M10.5 13.5h3v-3" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
          </button>
          <button
            type="button"
            className="runlog-status-hit"
            aria-expanded={logOpen || logExpanded}
            aria-controls="runlog-body"
            title={logOpen || logExpanded ? "Close run log" : "Open run log"}
            onClick={toggleLogPanel}
          >
            <span className="runlog-status">
              {busy ? <span className="spinner" aria-hidden="true" /> : null}
              {runLogStatus(visibleRunLog, busy && compactArmed)}
            </span>
          </button>
        </div>
        <div className="runlog-body" id="runlog-body" aria-hidden={!logOpen && !logExpanded}>
          {visibleRunLog.length === 0 ? (
            <p className="hint">Inventory, plan, tools, drafts, retries, and eval appear here while a turn runs.</p>
          ) : (
            <ol>
              {visibleRunLog.map((row, i) => (
                <li key={`${row.ts || i}-${row.step || "log"}-${i}`} className={`log-line ${row.step || ""} ${evalLogTone(row)}`.trim()}>
                  <span className="log-step">{row.step} ({i + 1})</span>
                  <span className="log-msg" title={(row.data && row.data.tags) || undefined}>
                    {logMessageParts(row).map((part, j) =>
                      typeof part === "string" ? (
                        part
                      ) : (
                        <strong key={`t-${i}-${j}`}>{part.text}</strong>
                      ),
                    )}
                  </span>
                </li>
              ))}
            </ol>
          )}
          <div ref={logEnd} />
        </div>
      </aside>

      <form className="composer" onSubmit={send}>
        {error && <p className="err">{error}</p>}
        <textarea
          rows={3}
          placeholder="Ask a CRE question, or attach a rent roll…"
          value={text}
          disabled={busy}
          onChange={(e) => {
            disarmCompactLog();
            setText(e.target.value);
          }}
        />
        <div className="chips" aria-label="Demo questions">
          {DEMO_CHIPS.map((chip) => (
            <button
              key={chip.id}
              type="button"
              className={`chip chip-${chip.level}${chipId === chip.id ? " on" : ""}`}
              disabled={busy}
              aria-pressed={chipId === chip.id}
              onClick={() => fillChip(chip)}
              title={chip.prompt}
            >
              <span className="chip-dot" aria-hidden="true" />
              {chip.label}
            </button>
          ))}
        </div>
        <div className="row">
          <label className="file">
            Attach CSV/xlsx
            <input
              ref={fileInput}
              type="file"
              accept=".csv,.xlsx,.xls"
              disabled={busy}
              onChange={(e) => {
                disarmCompactLog();
                setFile(e.target.files?.[0] || null);
              }}
            />
          </label>
          {file ? (
            <span className="fname">will attach {file.name}</span>
          ) : session?.vault ? (
            <span className="fname hint">saved in vault — not re-sent</span>
          ) : null}
          <label className="check">
            <input
              type="checkbox"
              checked={searchFail}
              disabled={busy}
              onChange={(e) => setSearchFail(e.target.checked)}
            />
            search-down
          </label>
          <button type="submit" disabled={busy}>
            {busy ? "Running…" : "Send"}
          </button>
        </div>
      </form>
    </div>
  );
}
