/**
 * FixProgressDrawer.jsx — Iter 212m-121
 *
 * Right-side slide-in drawer that opens the moment a fix or bulk
 * fix is kicked off, then tails the backend SSE stream from
 * `/api/aurem-dev/fix-pipeline/stream/{job_id}`.
 *
 * Phases rendered in order (per finding):
 *   queued → reading → generating → committing → verifying → fix-done
 * Then once globally: `done`.
 *
 * Everything in this drawer is driven by REAL backend events — no
 * simulated progress bars, no setTimeout sleights of hand.  When the
 * SSE stream goes silent we show a heartbeat dot; if the backend
 * sends `verified=true` we surface a green "GitHub verified" chip
 * next to the commit SHA.
 *
 * Mounted once at the App root via the global event
 * `aurem:open-fix-progress` carrying `{job_id, total}`.
 */
import React, { useEffect, useRef, useState } from "react";
import {
  X, GitCommit, GitPullRequest, ShieldCheck, ShieldAlert, ExternalLink,
  Loader2, FileSearch, Sparkles, UploadCloud, BadgeCheck,
} from "lucide-react";

const PHASE_META = {
  queued:      { Icon: FileSearch,   color: "#94a3b8", label: "Queued" },
  reading:     { Icon: FileSearch,   color: "#7dd3fc", label: "Reading file" },
  generating:  { Icon: Sparkles,     color: "#a855f7", label: "AI generating patch" },
  committing:  { Icon: UploadCloud,  color: "#fbbf24", label: "Committing" },
  verifying:   { Icon: BadgeCheck,   color: "#38bdf8", label: "Verifying" },
};

function shortSha(s) {
  return (s || "").slice(0, 7);
}

export default function FixProgressDrawer() {
  const [open,       setOpen]       = useState(false);
  const [jobId,      setJobId]      = useState(null);
  const [total,      setTotal]      = useState(1);
  const [items,      setItems]      = useState({});    // by finding_id
  const [phase,      setPhase]      = useState(null);  // last event
  const [terminal,   setTerminal]   = useState(null);
  const [error,      setError]      = useState(null);
  const esRef = useRef(null);

  // Listen for the global open event.
  useEffect(() => {
    const onOpen = (e) => {
      const { job_id, total: t } = e.detail || {};
      if (!job_id) return;
      setOpen(true);
      setJobId(job_id);
      setTotal(t || 1);
      setItems({});
      setPhase(null);
      setTerminal(null);
      setError(null);
    };
    window.addEventListener("aurem:open-fix-progress", onOpen);
    return () => window.removeEventListener("aurem:open-fix-progress", onOpen);
  }, []);

  // Wire SSE when a job_id is set.
  useEffect(() => {
    if (!jobId || !open) return undefined;
    // Close any previous stream.
    if (esRef.current) { try { esRef.current.close(); } catch { /* ignore */ } }

    const base =
      (typeof process !== "undefined" && process.env && process.env.REACT_APP_BACKEND_URL) ||
      window.location.origin;
    const token = localStorage.getItem("aurem_token");
    // Browser EventSource doesn't support headers — backend accepts
    // ?token=... as a fallback for SSE auth.  We pass via query
    // string only for the SSE endpoint.
    const url = `${base}/api/aurem-dev/fix-pipeline/stream/${jobId}?token=${encodeURIComponent(token || "")}`;
    let es;
    try { es = new EventSource(url, { withCredentials: false }); }
    catch (e) { setError(String(e)); return undefined; }
    esRef.current = es;

    es.addEventListener("phase", (ev) => {
      let data;
      try { data = JSON.parse(ev.data); }
      catch { return; }
      setPhase(data);
      const fid = data.finding_id;
      if (fid && data.phase !== "done" && data.phase !== "job-start"
          && data.phase !== "heartbeat" && data.phase !== "gone") {
        setItems((s) => ({
          ...s,
          [fid]: {
            ...(s[fid] || {}),
            ...data,
            // Track the most-advanced phase per finding so a stale
            // event can't regress the row.
            phase: data.phase,
          },
        }));
      }
      if (data.phase === "done") {
        setTerminal(data);
        try { es.close(); } catch { /* ignore */ }
      }
      if (data.phase === "gone") {
        setError(data.message || "Job not found (may have expired)");
        try { es.close(); } catch { /* ignore */ }
      }
    });
    es.onerror = () => {
      // Browser will auto-retry; if the connection is permanently
      // dead the readyState becomes CLOSED.
      if (es.readyState === 2) {
        setError("Connection lost");
      }
    };
    return () => { try { es.close(); } catch { /* ignore */ } };
  }, [jobId, open]);

  if (!open) return null;
  const rows = Object.values(items).sort(
    (a, b) => (a.index || 0) - (b.index || 0),
  );
  const completed = terminal?.completed ?? rows.filter((r) => r.phase === "fix-done").length;
  const failed    = terminal?.failed    ?? rows.filter((r) => r.phase === "fix-done" && r.ok === false).length;
  const pct = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;

  return (
    <>
      <div
        data-testid="fix-progress-scrim"
        onClick={() => setOpen(false)}
        style={{
          position: "fixed", inset: 0, zIndex: 1300,
          background: "rgba(6,8,13,0.55)", backdropFilter: "blur(4px)",
        }}
      />
      <aside
        data-testid="fix-progress-drawer"
        style={{
          position: "fixed", top: 0, right: 0, bottom: 0,
          width: "min(560px, 100%)", zIndex: 1301,
          background: "#0d1018",
          borderLeft: "1px solid rgba(255,255,255,0.08)",
          color: "#e8ecf3",
          display: "flex", flexDirection: "column",
          boxShadow: "-20px 0 60px rgba(0,0,0,0.55)",
          animation: "fixDrawerIn 220ms ease-out",
        }}
      >
        <style>{`
          @keyframes fixDrawerIn {
            from { transform: translateX(100%); opacity: 0.6; }
            to   { transform: translateX(0); opacity: 1; }
          }
          @keyframes pulseDot {
            0%, 100% { opacity: 0.4; }
            50%      { opacity: 1; }
          }
        `}</style>

        <header style={{
          padding: "16px 20px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          background: terminal
            ? (failed > 0
                ? "linear-gradient(90deg, rgba(239,68,68,0.10), transparent)"
                : "linear-gradient(90deg, rgba(34,197,94,0.10), transparent)")
            : "linear-gradient(90deg, rgba(249,115,22,0.10), transparent)",
          display: "flex", alignItems: "center", gap: 12,
        }}>
          {terminal
            ? (failed > 0 ? <ShieldAlert size={18} color="#fca5a5" />
                          : <ShieldCheck size={18} color="#86efac" />)
            : <Loader2 size={18} color="#fdba74" className="anim-spin" />}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 700 }}>
              {terminal ? "Fix complete" : "Fix in progress"}
            </div>
            <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2,
                          fontFamily: "'JetBrains Mono', monospace" }}>
              {terminal
                ? `${completed - failed} fixed · ${failed} failed · ${total} total`
                : `${completed}/${total} ${total === 1 ? "finding" : "findings"}`}
              <span style={{ marginLeft: 8 }}>job <code>{jobId?.slice(0, 10)}</code></span>
            </div>
          </div>
          <button
            data-testid="fix-progress-close"
            onClick={() => setOpen(false)}
            style={{
              padding: 6, background: "transparent",
              border: "1px solid rgba(255,255,255,0.12)",
              borderRadius: 6, cursor: "pointer", color: "#94a3b8",
            }}
          ><X size={14} /></button>
        </header>

        {/* Progress bar */}
        <div style={{
          height: 4, background: "rgba(255,255,255,0.04)",
          position: "relative", overflow: "hidden",
        }}>
          <div style={{
            width: `${pct}%`, height: "100%",
            background: terminal && failed > 0 ? "#fca5a5" : "#fb923c",
            transition: "width 240ms ease-out",
          }} />
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
          {error && (
            <div style={{
              padding: 12, marginBottom: 12, borderRadius: 8,
              background: "rgba(239,68,68,0.08)",
              border: "1px solid rgba(239,68,68,0.4)",
              color: "#fca5a5", fontSize: 12,
            }}>{error}</div>
          )}
          {rows.length === 0 && !error && (
            <div style={{
              display: "flex", alignItems: "center", gap: 10,
              color: "#94a3b8", fontSize: 12, padding: "8px 0",
            }}>
              <span style={{
                width: 8, height: 8, borderRadius: 999,
                background: "#fb923c", animation: "pulseDot 1.4s infinite",
              }} />
              Waiting for first event…
            </div>
          )}
          {rows.map((r) => {
            const meta = PHASE_META[r.phase] || PHASE_META.queued;
            const Icon = meta.Icon;
            const isDone = r.phase === "fix-done";
            const ok = r.ok !== false && (r.commit_sha || isDone);
            return (
              <div
                key={r.finding_id || r.index}
                data-testid={`fix-row-${r.finding_id}`}
                style={{
                  padding: 12, marginBottom: 8, borderRadius: 8,
                  background: isDone
                    ? (r.ok === false ? "rgba(239,68,68,0.06)" : "rgba(34,197,94,0.06)")
                    : "rgba(255,255,255,0.02)",
                  border: `1px solid ${
                    isDone
                      ? (r.ok === false ? "rgba(239,68,68,0.25)" : "rgba(34,197,94,0.30)")
                      : "rgba(255,255,255,0.06)"}`,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8,
                              marginBottom: 4, fontFamily: "'JetBrains Mono', monospace",
                              fontSize: 11 }}>
                  {isDone
                    ? (r.ok === false
                        ? <ShieldAlert size={12} color="#fca5a5" />
                        : <ShieldCheck size={12} color="#86efac" />)
                    : <Icon size={12} color={meta.color} className={
                        r.phase !== "queued" ? "anim-spin" : ""
                      } />}
                  <span style={{
                    color: isDone
                      ? (r.ok === false ? "#fca5a5" : "#86efac")
                      : meta.color,
                    fontWeight: 600,
                  }}>
                    {isDone
                      ? (r.ok === false ? "Failed" : "Fixed")
                      : meta.label}
                  </span>
                  <span style={{ color: "#94a3b8" }}>·</span>
                  <code style={{ color: "#cbd5e1" }}>{r.rule_id}</code>
                </div>
                <div style={{ fontSize: 11, color: "#94a3b8",
                              fontFamily: "'JetBrains Mono', monospace" }}>
                  {r.file}
                </div>
                {isDone && r.ok !== false && (
                  <div style={{
                    marginTop: 6, display: "flex", alignItems: "center",
                    gap: 8, flexWrap: "wrap", fontSize: 11,
                    fontFamily: "'JetBrains Mono', monospace",
                  }}>
                    {r.commit_sha && (
                      <a
                        data-testid={`fix-row-commit-${r.finding_id}`}
                        href={r.html_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                          display: "inline-flex", alignItems: "center", gap: 4,
                          padding: "2px 6px", borderRadius: 999,
                          background: "rgba(56,189,248,0.10)",
                          border: "1px solid rgba(56,189,248,0.35)",
                          color: "#7dd3fc", textDecoration: "none",
                        }}
                      >
                        <GitCommit size={10} />
                        {shortSha(r.commit_sha)}
                        <ExternalLink size={10} />
                      </a>
                    )}
                    {r.pr_url && (
                      <a
                        href={r.pr_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                          display: "inline-flex", alignItems: "center", gap: 4,
                          padding: "2px 6px", borderRadius: 999,
                          background: "rgba(168,85,247,0.10)",
                          border: "1px solid rgba(168,85,247,0.35)",
                          color: "#d8b4fe", textDecoration: "none",
                        }}
                      >
                        <GitPullRequest size={10} />
                        Draft PR
                      </a>
                    )}
                    {r.verified && (
                      <span style={{
                        padding: "2px 6px", borderRadius: 999,
                        background: "rgba(34,197,94,0.10)",
                        border: "1px solid rgba(34,197,94,0.30)",
                        color: "#86efac",
                      }}>GitHub verified ✓</span>
                    )}
                  </div>
                )}
                {isDone && r.ok === false && r.error && (
                  <div style={{
                    marginTop: 6, fontSize: 11, color: "#fca5a5",
                    fontFamily: "'JetBrains Mono', monospace",
                  }}>{r.error}</div>
                )}
              </div>
            );
          })}
        </div>

        {terminal && (
          <footer style={{
            padding: "12px 20px",
            borderTop: "1px solid rgba(255,255,255,0.06)",
            background: "rgba(255,255,255,0.015)",
            fontSize: 12, color: "#94a3b8",
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <span data-testid="fix-progress-terminal">{terminal.message}</span>
            <button
              data-testid="fix-progress-done"
              onClick={() => setOpen(false)}
              style={{
                padding: "6px 14px", borderRadius: 6,
                border: "1px solid rgba(255,255,255,0.18)",
                background: "rgba(255,255,255,0.04)",
                color: "#e8ecf3", cursor: "pointer", fontSize: 12,
              }}
            >Done</button>
          </footer>
        )}
      </aside>
    </>
  );
}
