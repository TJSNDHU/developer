/**
 * FixProgressDrawer.jsx — Iter 212m-147 (UI rewrite)
 *
 * Right-side slide-in drawer that tails the SSE stream from
 * `/api/aurem-dev/fix-pipeline/stream/{job_id}` and renders each fix
 * with REAL animated code diffs.
 *
 * NEW PHASES handled (backend Iter 212m-147):
 *   - fix-diff       → array of {type, line} dicts → animates +/- lines
 *                      into the active block with a 40 ms stagger
 *   - fix-committing → flips the active block to "Committing to GitHub…"
 *   - verifying      → flips to "Verifying commit on GitHub…"
 *   - fix-done       → collapses block into the completed list, shows
 *                      commit SHA + verified chip
 *   - done           → all-done summary card replaces active panel
 *
 * Preserved from earlier iters:
 *   - hydration from localStorage on mount (page refresh resilience)
 *   - restart button on orphaned/failed jobs (Iter 212m-128)
 *   - retry counter with last_error badge (Iter 212m-128)
 *   - heartbeat pulse dot + idle warnings (Iter 212m-128)
 *   - running mm:ss clock + event counter
 *
 * Mounted once at App root; opened via global event
 *   `aurem:open-fix-progress` carrying `{job_id, total}`.
 */
import React, { useEffect, useRef, useState, useCallback } from "react";
import {
  X, GitCommit, GitPullRequest, ShieldCheck, ShieldAlert, ExternalLink,
  Loader2, FileSearch, Sparkles, UploadCloud, BadgeCheck, RotateCw,
  ChevronDown, ChevronUp, CheckCircle2, AlertCircle,
} from "lucide-react";
import { api } from "../lib/api";

const PHASE_META = {
  queued:         { Icon: FileSearch,  color: "#94a3b8", label: "Queued" },
  reading:        { Icon: FileSearch,  color: "#7dd3fc", label: "Reading file" },
  generating:     { Icon: Sparkles,    color: "#a855f7", label: "AI generating patch" },
  "fix-diff":     { Icon: Sparkles,    color: "#c084fc", label: "Diff ready" },
  "fix-committing": { Icon: UploadCloud, color: "#fbbf24", label: "Committing to GitHub" },
  committing:     { Icon: UploadCloud, color: "#fbbf24", label: "Committing" },
  verifying:      { Icon: BadgeCheck,  color: "#38bdf8", label: "Verifying commit" },
};

const LS_JOB_KEY = "aurem_fix_active_job";

function shortSha(s) { return (s || "").slice(0, 7); }

function basename(p) {
  if (!p) return "";
  const i = p.lastIndexOf("/");
  return i >= 0 ? p.slice(i + 1) : p;
}

/* ─── Animated diff block ───────────────────────────────────────────
 * Renders an array of {type, line} entries as a syntax-tinted code
 * block.  Each line fades + slides in with a 40 ms stagger driven by
 * `--idx` so the user feels the AI typing.  Once a line is mounted
 * it stays put — re-renders don't replay the animation. */
function DiffBlock({ diff }) {
  if (!Array.isArray(diff) || diff.length === 0) return null;
  return (
    <div
      data-testid="fix-diff-block"
      style={{
        marginTop: 10,
        background: "#06080d",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 6,
        overflow: "hidden",
        maxHeight: 360,
        overflowY: "auto",
      }}
    >
      <div style={{
        padding: "6px 10px",
        background: "rgba(255,255,255,0.02)",
        borderBottom: "1px solid rgba(255,255,255,0.05)",
        fontSize: 10,
        color: "#64748b",
        fontFamily: "'JetBrains Mono', monospace",
        letterSpacing: "0.04em",
        textTransform: "uppercase",
      }}>
        Diff · {diff.filter((d) => d.type === "add").length} added · {diff.filter((d) => d.type === "remove").length} removed
      </div>
      <pre style={{
        margin: 0,
        padding: "8px 0",
        fontSize: 11,
        lineHeight: 1.55,
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        whiteSpace: "pre",
        overflow: "auto",
      }}>
        {diff.map((d, idx) => {
          const isAdd     = d.type === "add";
          const isRemove  = d.type === "remove";
          const isHunk    = d.type === "hunk";
          let prefix = "  ";
          let color  = "#94a3b8";
          let bg     = "transparent";
          if (isAdd)    { prefix = "+ "; color = "#86efac"; bg = "rgba(34,197,94,0.07)"; }
          if (isRemove) { prefix = "- "; color = "#fca5a5"; bg = "rgba(239,68,68,0.07)"; }
          if (isHunk)   { prefix = "  "; color = "#7dd3fc"; bg = "rgba(56,189,248,0.05)"; }
          return (
            <div
              key={idx}
              data-testid={`fix-diff-line-${idx}`}
              data-type={d.type}
              style={{
                display: "grid",
                gridTemplateColumns: "20px 1fr",
                padding: "0 10px",
                background: bg,
                color,
                opacity: 0,
                transform: "translateY(4px)",
                animation: "diffLineIn 240ms ease-out forwards",
                animationDelay: `${Math.min(idx, 40) * 40}ms`,
              }}
            >
              <span style={{ color: isAdd ? "#4ade80" : isRemove ? "#f87171" : "#475569", userSelect: "none" }}>
                {prefix}
              </span>
              <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                {d.line || " "}
              </span>
            </div>
          );
        })}
      </pre>
    </div>
  );
}

/* ─── Active fix card (top of body) ──────────────────────────────── */
function ActiveFixCard({ row, fixIndex, fixTotal }) {
  if (!row) return null;
  const phase = row.phase || "queued";
  const meta  = PHASE_META[phase] || PHASE_META.queued;
  const Icon  = meta.Icon;
  const stageBadge = (() => {
    if (phase === "fix-committing" || phase === "committing")
      return { label: "COMMITTING", bg: "rgba(251,191,36,0.12)", border: "rgba(251,191,36,0.40)", color: "#fde68a" };
    if (phase === "verifying")
      return { label: "VERIFYING",  bg: "rgba(56,189,248,0.12)", border: "rgba(56,189,248,0.40)", color: "#7dd3fc" };
    if (phase === "fix-diff")
      return { label: "PATCH READY", bg: "rgba(192,132,252,0.12)", border: "rgba(192,132,252,0.40)", color: "#d8b4fe" };
    if (phase === "generating")
      return { label: "GENERATING", bg: "rgba(168,85,247,0.12)", border: "rgba(168,85,247,0.40)", color: "#d8b4fe" };
    if (phase === "reading")
      return { label: "READING",    bg: "rgba(125,211,252,0.12)", border: "rgba(125,211,252,0.40)", color: "#bae6fd" };
    if (phase === "retrying")
      return { label: `RETRY ${row.attempt}/${row.attempts_of || 3}`, bg: "rgba(250,204,21,0.12)", border: "rgba(250,204,21,0.40)", color: "#fde68a" };
    return { label: "QUEUED", bg: "rgba(148,163,184,0.12)", border: "rgba(148,163,184,0.30)", color: "#cbd5e1" };
  })();

  return (
    <div
      data-testid="fix-active-card"
      style={{
        marginBottom: 12,
        padding: 14,
        borderRadius: 10,
        background: "linear-gradient(180deg, rgba(251,146,60,0.06), rgba(251,146,60,0.015))",
        border: "1px solid rgba(251,146,60,0.30)",
        boxShadow: "0 0 24px -8px rgba(251,146,60,0.25)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <Icon size={14} color={meta.color} className={phase !== "queued" ? "anim-spin" : ""} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
            fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
          }}>
            <span style={{
              padding: "2px 8px", borderRadius: 999,
              background: stageBadge.bg,
              border: `1px solid ${stageBadge.border}`,
              color: stageBadge.color,
              fontWeight: 700, fontSize: 9, letterSpacing: "0.06em",
            }}>{stageBadge.label}</span>
            <span style={{
              padding: "2px 8px", borderRadius: 999,
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.10)",
              color: "#cbd5e1", fontWeight: 600, fontSize: 9,
            }}>FIX {fixIndex}/{fixTotal}</span>
            <code style={{ color: "#fdba74" }}>{row.rule_id || "unknown"}</code>
          </div>
          <div style={{
            marginTop: 4,
            fontSize: 11,
            color: "#cbd5e1",
            fontFamily: "'JetBrains Mono', monospace",
            wordBreak: "break-all",
          }}>{row.file || "—"}</div>
        </div>
      </div>
      {row.diff && row.diff.length > 0 && <DiffBlock diff={row.diff} />}
      {(phase === "fix-committing" || phase === "committing" || phase === "verifying") && (
        <div style={{
          marginTop: 10, padding: 8, borderRadius: 6,
          background: "rgba(251,191,36,0.05)",
          border: "1px solid rgba(251,191,36,0.20)",
          display: "flex", alignItems: "center", gap: 8,
          fontSize: 11, color: "#fde68a",
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          <Loader2 size={12} className="anim-spin" />
          {phase === "verifying"
            ? "Verifying commit lands on GitHub…"
            : "Pushing commit to GitHub…"}
          {row.commit_sha && (
            <code style={{ marginLeft: "auto", color: "#7dd3fc" }}>
              {shortSha(row.commit_sha)}
            </code>
          )}
        </div>
      )}
      {row.last_error && phase === "retrying" && (
        <div
          data-testid={`fix-active-retry-${row.finding_id}`}
          style={{
            marginTop: 10, padding: 8, borderRadius: 6,
            background: "rgba(250,204,21,0.06)",
            border: "1px solid rgba(250,204,21,0.25)",
            color: "#fde68a",
            fontSize: 11,
            fontFamily: "'JetBrains Mono', monospace",
            wordBreak: "break-word",
          }}
          title={row.last_error}
        >
          ⚠ {row.last_error.slice(0, 240)}
        </div>
      )}
    </div>
  );
}

/* ─── Completed fix row (collapsed by default) ───────────────────── */
function CompletedRow({ row }) {
  const [expanded, setExpanded] = useState(false);
  const isFailed = row.ok === false;
  return (
    <div
      data-testid={`fix-row-${row.finding_id}`}
      style={{
        padding: 10, marginBottom: 6, borderRadius: 8,
        background: isFailed ? "rgba(239,68,68,0.05)" : "rgba(34,197,94,0.04)",
        border: `1px solid ${isFailed ? "rgba(239,68,68,0.22)" : "rgba(34,197,94,0.22)"}`,
      }}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        style={{
          width: "100%", background: "transparent", border: 0, padding: 0,
          cursor: "pointer", color: "inherit", textAlign: "left",
          display: "flex", alignItems: "center", gap: 8,
          fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
        }}
      >
        {isFailed
          ? <ShieldAlert size={12} color="#fca5a5" />
          : <ShieldCheck size={12} color="#86efac" />}
        <span style={{ color: isFailed ? "#fca5a5" : "#86efac", fontWeight: 700, fontSize: 10, letterSpacing: "0.04em" }}>
          {isFailed ? "FAILED" : "FIXED"}
        </span>
        <code style={{ color: "#cbd5e1" }}>{row.rule_id || "unknown"}</code>
        <span style={{
          marginLeft: "auto", color: "#64748b", fontSize: 10,
        }}>{basename(row.file)}</span>
        {expanded
          ? <ChevronUp size={11} color="#64748b" />
          : <ChevronDown size={11} color="#64748b" />}
      </button>
      <div style={{
        marginTop: 6, fontSize: 10, color: "#94a3b8",
        fontFamily: "'JetBrains Mono', monospace",
        wordBreak: "break-all",
      }}>
        {row.file}
      </div>
      {!isFailed && (
        <div style={{
          marginTop: 6, display: "flex", alignItems: "center",
          gap: 6, flexWrap: "wrap", fontSize: 11,
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          {row.commit_sha && (
            <a
              data-testid={`fix-row-commit-${row.finding_id}`}
              href={row.html_url}
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
              {shortSha(row.commit_sha)}
              <ExternalLink size={10} />
            </a>
          )}
          {row.pr_url && (
            <a
              href={row.pr_url} target="_blank" rel="noopener noreferrer"
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
          {row.verified && (
            <span style={{
              padding: "2px 6px", borderRadius: 999,
              background: "rgba(34,197,94,0.10)",
              border: "1px solid rgba(34,197,94,0.30)",
              color: "#86efac",
            }}>GitHub verified ✓</span>
          )}
        </div>
      )}
      {isFailed && row.error && (
        <div style={{
          marginTop: 6, fontSize: 11, color: "#fca5a5",
          fontFamily: "'JetBrains Mono', monospace",
          wordBreak: "break-word",
        }}>{row.error}</div>
      )}
      {expanded && row.diff && row.diff.length > 0 && (
        <DiffBlock diff={row.diff} />
      )}
    </div>
  );
}

/* ─── Final summary card (terminal state) ──────────────────────────── */
function FinalSummaryCard({ terminal, completed, failed, total, durationStr, jobId }) {
  const allOk = failed === 0 && completed > 0;
  return (
    <div
      data-testid="fix-final-summary"
      style={{
        marginBottom: 12, padding: 18, borderRadius: 12,
        background: allOk
          ? "linear-gradient(180deg, rgba(34,197,94,0.10), rgba(34,197,94,0.02))"
          : (failed > 0
            ? "linear-gradient(180deg, rgba(239,68,68,0.10), rgba(239,68,68,0.02))"
            : "linear-gradient(180deg, rgba(148,163,184,0.08), rgba(148,163,184,0.02))"),
        border: `1px solid ${allOk
          ? "rgba(34,197,94,0.32)"
          : (failed > 0 ? "rgba(239,68,68,0.32)" : "rgba(148,163,184,0.30)")}`,
      }}
    >
      <div style={{
        display: "flex", alignItems: "center", gap: 10, marginBottom: 10,
      }}>
        {allOk
          ? <CheckCircle2 size={22} color="#86efac" />
          : <AlertCircle size={22} color={failed > 0 ? "#fca5a5" : "#94a3b8"} />}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: 14, fontWeight: 700,
            color: allOk ? "#86efac" : (failed > 0 ? "#fca5a5" : "#cbd5e1"),
          }}>
            {allOk
              ? "All findings fixed"
              : (failed > 0 ? "Completed with failures" : "Nothing to do")}
          </div>
          <div style={{
            marginTop: 2, fontSize: 11, color: "#94a3b8",
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            {completed - failed} fixed · {failed} failed · {total} total
            {durationStr && <> · ⏱ {durationStr}</>}
          </div>
        </div>
      </div>
      {terminal?.message && (
        <div style={{
          fontSize: 11, color: "#cbd5e1", lineHeight: 1.5,
          fontFamily: "'JetBrains Mono', monospace",
        }}>{terminal.message}</div>
      )}
      <div style={{
        marginTop: 8, fontSize: 10, color: "#64748b",
        fontFamily: "'JetBrains Mono', monospace",
      }}>job <code>{jobId?.slice(0, 12)}</code></div>
    </div>
  );
}


export default function FixProgressDrawer() {
  const [open,       setOpen]       = useState(false);
  const [jobId,      setJobId]      = useState(null);
  const [total,      setTotal]      = useState(1);
  const [items,      setItems]      = useState({});    // by finding_id
  const [activeId,   setActiveId]   = useState(null);  // currently-streaming finding
  const [terminal,   setTerminal]   = useState(null);
  const [error,      setError]      = useState(null);
  const [canRestart, setCanRestart] = useState(false);
  const [hydrated,   setHydrated]   = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [startedAt,   setStartedAt]   = useState(null);
  const [endedAt,     setEndedAt]     = useState(null);
  const [now,         setNow]         = useState(() => Date.now());
  const [lastEventAt, setLastEventAt] = useState(null);
  const [eventCount,  setEventCount]  = useState(0);
  const esRef = useRef(null);

  const resetAll = useCallback((preserveOpen = true) => {
    setItems({});
    setActiveId(null);
    setTerminal(null);
    setError(null);
    setCanRestart(false);
    setHydrated(false);
    const t0 = Date.now();
    setStartedAt(t0);
    setEndedAt(null);
    setLastEventAt(t0);
    setEventCount(0);
    setNow(t0);
    if (!preserveOpen) setOpen(true);
  }, []);

  const handleRestart = useCallback(async () => {
    if (!jobId || restarting) return;
    setRestarting(true);
    setError(null);
    try {
      const r = await api.post(`/fix-pipeline/restart/${jobId}`);
      const next = r.data || {};
      if (next.nothing_to_do) {
        setTerminal({
          phase: "done", ok: true,
          message: next.message || "All findings already complete.",
        });
        setRestarting(false);
        try { localStorage.removeItem(LS_JOB_KEY); } catch { /* ignore */ }
        return;
      }
      resetAll(true);
      setTotal(next.remaining || 1);
      setJobId(next.job_id);
      try { localStorage.setItem(LS_JOB_KEY,
        JSON.stringify({ job_id: next.job_id, total: next.remaining }));
      } catch { /* ignore */ }
    } catch (e) {
      const msg = e?.response?.data?.detail?.message
                  || e?.response?.data?.detail
                  || e?.message
                  || "Restart failed";
      setError(typeof msg === "string" ? msg : JSON.stringify(msg));
    } finally {
      setRestarting(false);
    }
  }, [jobId, restarting, resetAll]);

  // ── Mount: re-attach to any in-flight job (page-refresh recovery) ──
  useEffect(() => {
    try {
      const raw = localStorage.getItem(LS_JOB_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!parsed?.job_id) return;
      setOpen(true);
      setJobId(parsed.job_id);
      setTotal(parsed.total || 1);
      resetAll(true);
    } catch { /* ignore */ }
  }, [resetAll]);

  // ── Listen for the global open event ──
  useEffect(() => {
    const onOpen = (e) => {
      const { job_id, total: t } = e.detail || {};
      if (!job_id) return;
      setOpen(true);
      setJobId(job_id);
      setTotal(t || 1);
      resetAll(true);
      try { localStorage.setItem(LS_JOB_KEY,
        JSON.stringify({ job_id, total: t || 1 }));
      } catch { /* ignore */ }
    };
    window.addEventListener("aurem:open-fix-progress", onOpen);
    return () => window.removeEventListener("aurem:open-fix-progress", onOpen);
  }, [resetAll]);

  // ── One-second tick for the running clock ──
  useEffect(() => {
    if (!open || terminal) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [open, terminal]);

  // ── SSE wire-up ──
  useEffect(() => {
    if (!jobId || !open) return undefined;
    if (esRef.current) { try { esRef.current.close(); } catch { /* ignore */ } }

    const base =
      (typeof process !== "undefined" && process.env && process.env.REACT_APP_BACKEND_URL) ||
      window.location.origin;
    const token = localStorage.getItem("aurem_token");
    const url = `${base}/api/aurem-dev/fix-pipeline/stream/${jobId}?token=${encodeURIComponent(token || "")}`;
    let es;
    try { es = new EventSource(url, { withCredentials: false }); }
    catch (e) { setError(String(e)); return undefined; }
    esRef.current = es;

    es.addEventListener("phase", (ev) => {
      let data;
      try { data = JSON.parse(ev.data); }
      catch { return; }
      setLastEventAt(Date.now());
      setEventCount((c) => c + 1);
      const fid = data.finding_id;
      const ph  = data.phase;

      // Set/clear active finding pointer based on lifecycle event.
      if (fid && ph && ph !== "done" && ph !== "job-start"
          && ph !== "heartbeat" && ph !== "gone" && ph !== "fix-done"
          && ph !== "hydrated" && ph !== "job-error") {
        setActiveId(fid);
      }

      if (fid && ph !== "done" && ph !== "job-start"
          && ph !== "heartbeat" && ph !== "gone"
          && ph !== "hydrated" && ph !== "job-error") {
        setItems((s) => {
          const prev = s[fid] || {};
          const merged = {
            ...prev,
            ...data,
            phase: ph,
          };
          // Retry counter — keep last_error + attempt around for the
          // active card's amber strip.
          if (ph === "retrying") {
            merged.attempt     = data.attempt;
            merged.attempts_of = data.of ?? prev.attempts_of;
            merged.last_error  = data.last_error;
          }
          // Iter 212m-147 — diff payload accumulates onto the row so
          // it's still visible when the phase moves on to
          // fix-committing / verifying.
          if (ph === "fix-diff" && Array.isArray(data.diff)) {
            merged.diff = data.diff;
          }
          return { ...s, [fid]: merged };
        });
      }

      // Iter 212m-128 — fan-out for parent list refresh.
      if (ph === "fix-done" && data.ok === true) {
        try {
          window.dispatchEvent(new CustomEvent("aurem:finding-fixed", {
            detail: {
              finding_id: data.finding_id,
              rule_id:    data.rule_id,
              commit_sha: data.commit_sha,
              html_url:   data.html_url,
              file:       data.file,
            },
          }));
        } catch { /* ignore */ }
      }
      // Iter 212m-147 — fix-done collapses the active card into the
      // completed list.  We clear activeId so the next reading/diff
      // event for the next finding owns the spotlight.
      if (ph === "fix-done") {
        setActiveId((prev) => (prev === fid ? null : prev));
      }

      if (ph === "done") {
        setTerminal(data);
        setEndedAt(Date.now());
        setActiveId(null);
        if (data.status && data.status !== "done") {
          setCanRestart(data.can_restart !== false);
        }
        try { es.close(); } catch { /* ignore */ }
        try { localStorage.removeItem(LS_JOB_KEY); } catch { /* ignore */ }
      }
      if (ph === "job-error") {
        setError(data.message || data.error || "Worker error");
        setCanRestart(data.can_restart !== false);
      }
      if (ph === "hydrated") {
        setHydrated(true);
        setTerminal({
          phase:     "done",
          ok:        data.status === "done",
          completed: data.completed || 0,
          failed:    data.failed || 0,
          total:     data.total || 0,
          results:   data.results || [],
          message:   data.message || "Resumed from history.",
          status:    data.status,
        });
        setEndedAt(Date.now());
        const itemsFromResults = {};
        (data.results || []).forEach((r, idx) => {
          itemsFromResults[r.finding_id || `idx_${idx}`] = {
            phase:      "fix-done",
            finding_id: r.finding_id,
            ok:         r.ok,
            commit_sha: r.commit_sha,
            html_url:   r.html_url,
            file:       r.file,
            rule_id:    r.rule_id,
            error:      r.error,
            index:      idx,
          };
        });
        setItems(itemsFromResults);
        setCanRestart(data.can_restart === true);
        try { es.close(); } catch { /* ignore */ }
      }
      if (ph === "gone") {
        setError(data.message || "Job not found (may have expired)");
        setCanRestart(data.can_restart === true);
        try { es.close(); } catch { /* ignore */ }
        try { localStorage.removeItem(LS_JOB_KEY); } catch { /* ignore */ }
      }
    });
    es.onerror = () => {
      if (es.readyState === 2) {
        setError("Connection lost");
      }
    };
    return () => { try { es.close(); } catch { /* ignore */ } };
  }, [jobId, open]);

  if (!open) return null;

  const allRows = Object.values(items).sort(
    (a, b) => (a.index || 0) - (b.index || 0),
  );
  const completedRows = allRows.filter((r) => r.phase === "fix-done");
  const activeRow     = activeId ? items[activeId] : null;

  const completed = terminal?.completed ?? completedRows.length;
  const failed    = terminal?.failed    ?? completedRows.filter((r) => r.ok === false).length;
  const pct = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  const remaining = Math.max(0, (total || 0) - completed);

  const elapsedRef = endedAt ?? now;
  const elapsedMs  = startedAt ? elapsedRef - startedAt : 0;
  const elapsedStr = (() => {
    const s = Math.floor(elapsedMs / 1000);
    const m = Math.floor(s / 60);
    return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  })();
  const idleMs = lastEventAt ? now - lastEventAt : 0;
  const pulseTone = (() => {
    if (terminal) return "done";
    if (idleMs < 2000)  return "alive";
    if (idleMs < 30000) return "slow";
    return "stuck";
  })();

  // Index in the running batch — driven by the current row if any,
  // else by completed count.
  const activeFixIndex = activeRow?.fix_index
    ?? (completedRows.length + 1);
  const activeFixTotal = activeRow?.fix_total ?? total;

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
          width: "min(620px, 100%)", zIndex: 1301,
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
          @keyframes diffLineIn {
            from { opacity: 0; transform: translateY(4px); }
            to   { opacity: 1; transform: translateY(0); }
          }
          @keyframes progressShine {
            0%   { background-position: -200px 0; }
            100% { background-position: 200px 0; }
          }
          .anim-spin { animation: spin 1s linear infinite; }
          @keyframes spin { to { transform: rotate(360deg); } }
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
            <div style={{ fontSize: 14, fontWeight: 700,
                          display: "flex", alignItems: "center", gap: 10 }}>
              <span>{terminal ? "Fix complete" : "Fix in progress"}</span>
              <span
                data-testid={`fix-progress-pulse-${pulseTone}`}
                style={{
                  width: 8, height: 8, borderRadius: 999,
                  background: pulseTone === "alive" ? "#86efac"
                            : pulseTone === "slow"  ? "#fde68a"
                            : pulseTone === "stuck" ? "#fca5a5"
                            : "#475569",
                  boxShadow: pulseTone === "alive"
                    ? "0 0 0 0 rgba(134,239,172,0.7)"
                    : "none",
                  animation: pulseTone === "alive"
                    ? "pulseDot 1.0s infinite" : (
                    pulseTone === "slow"  ? "pulseDot 2.0s infinite" : "none"
                  ),
                }}
              />
            </div>
            <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2,
                          fontFamily: "'JetBrains Mono', monospace",
                          display: "flex", alignItems: "center", gap: 10,
                          flexWrap: "wrap" }}>
              <span data-testid="fix-progress-counter">
                {terminal
                  ? `${completed - failed} fixed · ${failed} failed · ${total} total`
                  : `${completed}/${total} · ${remaining} remaining`}
              </span>
              <span data-testid="fix-progress-clock"
                    style={{ color: terminal ? "#94a3b8" : "#fdba74" }}>
                ⏱ {elapsedStr}
              </span>
              <span data-testid="fix-progress-events">
                {eventCount} events
              </span>
              {!terminal && pulseTone === "slow" && (
                <span style={{ color: "#fde68a" }}>still working…</span>
              )}
              {!terminal && pulseTone === "stuck" && (
                <span style={{ color: "#fca5a5" }}>
                  connection slow — {Math.floor(idleMs / 1000)}s idle
                </span>
              )}
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

        {/* Animated progress bar (with shine effect while running) */}
        <div style={{
          height: 4, background: "rgba(255,255,255,0.04)",
          position: "relative", overflow: "hidden",
        }}>
          <div
            data-testid="fix-progress-bar"
            style={{
              width: `${pct}%`, height: "100%",
              background: terminal && failed > 0
                ? "linear-gradient(90deg, #fca5a5, #f87171, #fca5a5)"
                : terminal
                  ? "linear-gradient(90deg, #4ade80, #86efac, #4ade80)"
                  : "linear-gradient(90deg, #fb923c, #fdba74, #fb923c)",
              backgroundSize: "200px 100%",
              animation: terminal ? "none" : "progressShine 1.6s linear infinite",
              transition: "width 280ms ease-out",
            }}
          />
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

          {terminal && (
            <FinalSummaryCard
              terminal={terminal}
              completed={completed}
              failed={failed}
              total={total}
              durationStr={elapsedStr}
              jobId={jobId}
            />
          )}

          {!terminal && activeRow && (
            <ActiveFixCard
              row={activeRow}
              fixIndex={activeFixIndex}
              fixTotal={activeFixTotal}
            />
          )}

          {!terminal && !activeRow && allRows.length === 0 && !error && (
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

          {/* Completed fixes list */}
          {completedRows.length > 0 && (
            <div data-testid="fix-completed-list">
              <div style={{
                fontSize: 10, color: "#64748b",
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.06em", textTransform: "uppercase",
                marginBottom: 8, marginTop: terminal ? 0 : 14,
              }}>
                Completed · {completedRows.length}
              </div>
              {completedRows.map((r) => (
                <CompletedRow key={r.finding_id || r.index} row={r} />
              ))}
            </div>
          )}
        </div>

        {terminal && (
          <footer
            data-testid="fix-progress-footer"
            style={{
              padding: "12px 20px",
              borderTop: "1px solid rgba(255,255,255,0.06)",
              background: "rgba(255,255,255,0.015)",
              fontSize: 12, color: "#94a3b8",
              display: "flex", justifyContent: "space-between", alignItems: "center",
              gap: 12, flexWrap: "wrap",
            }}>
            <span data-testid="fix-progress-terminal">
              {hydrated && (
                <span style={{
                  marginRight: 8, padding: "2px 6px", borderRadius: 999,
                  background: "rgba(56,189,248,0.10)",
                  border: "1px solid rgba(56,189,248,0.30)",
                  color: "#7dd3fc", fontSize: 10,
                }}>RESUMED</span>
              )}
              {completed - failed} of {total} {total === 1 ? "fix" : "fixes"} committed
            </span>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              {canRestart && (
                <button
                  data-testid="fix-progress-restart"
                  onClick={handleRestart}
                  disabled={restarting}
                  style={{
                    padding: "6px 14px", borderRadius: 6,
                    border: "1px solid rgba(251,146,60,0.40)",
                    background: restarting
                      ? "rgba(251,146,60,0.05)"
                      : "rgba(251,146,60,0.12)",
                    color: "#fdba74",
                    cursor: restarting ? "wait" : "pointer",
                    fontSize: 12, fontWeight: 600,
                    display: "inline-flex", alignItems: "center", gap: 6,
                  }}
                ><RotateCw size={12} className={restarting ? "anim-spin" : ""} />
                  {restarting ? "Restarting…" : "Restart remaining"}</button>
              )}
              <button
                data-testid="fix-progress-done"
                onClick={() => {
                  setOpen(false);
                  try { localStorage.removeItem(LS_JOB_KEY); }
                  catch { /* ignore */ }
                }}
                style={{
                  padding: "6px 14px", borderRadius: 6,
                  border: "1px solid rgba(255,255,255,0.18)",
                  background: "rgba(255,255,255,0.04)",
                  color: "#e8ecf3", cursor: "pointer", fontSize: 12,
                }}
              >Done</button>
            </div>
          </footer>
        )}

        {/* Inline restart strip when a job-error arrives mid-flight */}
        {!terminal && canRestart && (
          <div
            data-testid="fix-progress-mid-error-restart"
            style={{
              padding: "10px 16px",
              borderTop: "1px solid rgba(239,68,68,0.20)",
              background: "rgba(239,68,68,0.05)",
              fontSize: 12,
              display: "flex", justifyContent: "space-between",
              alignItems: "center", gap: 12, flexWrap: "wrap",
            }}>
            <span style={{ color: "#fca5a5" }}>
              Worker crashed — restart to retry the remaining findings.
            </span>
            <button
              data-testid="fix-progress-mid-error-restart-btn"
              onClick={handleRestart}
              disabled={restarting}
              style={{
                padding: "6px 12px", borderRadius: 6,
                border: "1px solid rgba(251,146,60,0.40)",
                background: "rgba(251,146,60,0.12)",
                color: "#fdba74",
                cursor: restarting ? "wait" : "pointer",
                fontSize: 12, fontWeight: 600,
                display: "inline-flex", alignItems: "center", gap: 6,
              }}
            ><RotateCw size={12} className={restarting ? "anim-spin" : ""} />
              {restarting ? "Restarting…" : "Restart"}</button>
          </div>
        )}
      </aside>
    </>
  );
}
