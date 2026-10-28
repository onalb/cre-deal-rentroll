import { useEffect, useRef, useState } from "react";

const FILE_NAME = "La Cordoba Rent Roll 04-20-21 (2) 164.xlsx";

function fmt(stats) {
  if (!stats || stats.n === 0 || stats.mean == null) return "—";
  return Number(stats.mean).toFixed(2);
}

function fmtStd(stats) {
  if (!stats || stats.n === 0 || stats.stdev == null) return "—";
  return Number(stats.stdev).toFixed(2);
}

function scoreText(value) {
  return value == null ? "—" : String(value);
}

function mergeRows(cases, summary) {
  const scores = new Map(summary.map((row) => [row.id, row]));
  if (!cases.length) return summary.map((row) => ({ query: "", ...row }));
  const ids = new Set(cases.map((row) => row.id));
  const merged = cases.map((row) => ({
    ...(scores.get(row.id) || {}),
    id: row.id,
    query: row.query,
  }));
  for (const row of summary) {
    if (!ids.has(row.id)) merged.push({ query: "", ...row });
  }
  return merged;
}

function CaseCell({ row, open, onOpen, onClose }) {
  const nameRef = useRef(null);
  const closeRef = useRef(null);

  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  function close() {
    onClose();
    nameRef.current?.focus();
  }

  return (
    <td className={open ? "case-open" : undefined}>
      <span className="case-anchor">
        <button
          ref={nameRef}
          type="button"
          className="case-name"
          aria-expanded={open}
          aria-controls={open ? `case-tip-${row.id}` : undefined}
          disabled={!row.query}
          onClick={() => (open ? close() : onOpen(row.id))}
        >
          {row.id}
        </button>
        {open && (
          <div
            id={`case-tip-${row.id}`}
            className="case-tip"
            role="dialog"
            aria-label={`${row.id} user prompt`}
          >
            <p>{row.query}</p>
            <button ref={closeRef} type="button" className="case-tip-close" onClick={close}>
              Close
            </button>
          </div>
        )}
      </span>
    </td>
  );
}

export default function EvalPage({ username, onBack, hidden = false }) {
  const [runs, setRuns] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("Idle");
  const [log, setLog] = useState([]);
  const [summary, setSummary] = useState([]);
  const [cases, setCases] = useState([]);
  const [casesNote, setCasesNote] = useState("Loading gold cases…");
  const [casesError, setCasesError] = useState(false);
  const [openId, setOpenId] = useState(null);

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const res = await fetch("/api/eval/cases", {
          headers: { Accept: "application/json", "X-Username": username },
        });
        const data = await res.json().catch(() => null);
        if (!res.ok) {
          const detail = data?.detail || data?.message || `Could not load cases (${res.status})`;
          throw new Error(typeof detail === "string" ? detail : "Could not load cases");
        }
        if (cancel) return;
        setCases(Array.isArray(data) ? data : []);
        setCasesNote("");
        setCasesError(false);
      } catch (err) {
        if (!cancel) {
          setCasesNote(err.message || "Could not load cases");
          setCasesError(true);
        }
      }
    })();
    return () => {
      cancel = true;
    };
  }, [username]);

  useEffect(() => {
    if (!openId) return undefined;
    function onKey(ev) {
      if (ev.key === "Escape") setOpenId(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openId]);

  async function start(e) {
    e.preventDefault();
    if (busy) return;
    const times = Number(runs);
    if (!Number.isInteger(times) || times < 1 || times > 5) {
      setError("Enter a whole number from 1 to 5.");
      return;
    }
    setBusy(true);
    setError("");
    setLog([]);
    setSummary([]);
    setStatus("Starting");
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 60 * 60 * 1000);
    try {
      const res = await fetch("/api/eval/run", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          "X-Username": username,
        },
        body: JSON.stringify({ runs: times }),
        signal: ctrl.signal,
      });
      if (!res.ok || !(res.headers.get("content-type") || "").includes("event-stream")) {
        const data = await res.json().catch(() => ({}));
        const detail = data.detail || data.message || `Eval did not start (${res.status})`;
        throw new Error(typeof detail === "string" ? detail : "Eval did not start");
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
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
          if (data.summary) setSummary(data.summary);
          if (data.message) setStatus(data.message);
          if (event === "run") {
            setLog((prev) => [...prev, data]);
          }
          if (event === "error") {
            setError(data.message || "Eval stopped");
          }
        }
      }
    } catch (err) {
      setError(err?.name === "AbortError" ? "That took too long." : err.message || "Eval failed");
    } finally {
      clearTimeout(timer);
      setBusy(false);
    }
  }

  const rows = mergeRows(cases, summary);

  return (
    <div className="app" hidden={hidden}>
      <header className="banner">
        <div className="banner-inner">
          <div>
            <h1>Gold eval</h1>
            <p>Score each gold case with the live judge.</p>
          </div>
          <div className="who">
            <span>{username}</span>
            <button className="ghost" type="button" onClick={onBack}>
              Deal chat
            </button>
          </div>
        </div>
      </header>
      <main>
      <section className="eval-panel">
        <p className="lede">
          Each start uploads {FILE_NAME} through the same ingest as chat, then runs
          every gold case that many times. Scores are G, C, and R from the live judge.
        </p>
        <form onSubmit={start} className="row">
          <label>
            Runs per case
            <input
              type="number"
              min={1}
              max={5}
              step={1}
              value={runs}
              disabled={busy}
              onChange={(ev) => setRuns(ev.target.value)}
            />
          </label>
          <button type="submit" disabled={busy}>
            {busy ? "Running…" : "Start"}
          </button>
          <span className="eval-status" aria-live="polite">
            {busy && <span className="spinner" aria-hidden="true" />}
            {status}
          </span>
        </form>
        {error && <p className="err">{error}</p>}
        {casesError && <p className="err">{casesNote}</p>}
        <table className="eval-table">
          <thead>
            <tr>
              <th>Case</th>
              <th>Scored</th>
              <th>G mean</th>
              <th>G stdev</th>
              <th>C mean</th>
              <th>C stdev</th>
              <th>R mean</th>
              <th>R stdev</th>
              <th>Tools</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={9}>
                  {casesNote || "Means and standard deviations show up as runs finish."}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.id}>
                  <CaseCell
                    row={row}
                    open={openId === row.id}
                    onOpen={setOpenId}
                    onClose={() => setOpenId(null)}
                  />
                  <td>{row.G ? `${row.G.n}/${row.runs}` : "—"}</td>
                  <td>{fmt(row.G)}</td>
                  <td>{fmtStd(row.G)}</td>
                  <td>{fmt(row.C)}</td>
                  <td>{fmtStd(row.C)}</td>
                  <td>{fmt(row.R)}</td>
                  <td>{fmtStd(row.R)}</td>
                  <td>{row.tools_passed == null ? "—" : `${row.tools_passed}/${row.runs}`}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        <ol className="eval-log">
          {log.map((row, i) => (
            <li key={`${row.case}-${row.run}-${i}`}>
              <strong>
                {row.case} · run {row.run} of {row.runs}
              </strong>
              {" · "}
              G {scoreText(row.G)} · C {scoreText(row.C)} · R {scoreText(row.R)}
              {" · "}
              tools {row.tools_ok ? "ok" : "miss"}
              {row.kind ? ` · ${row.kind}` : ""}
              {row.error ? ` · ${row.error}` : ""}
            </li>
          ))}
        </ol>
      </section>
      </main>
    </div>
  );
}
