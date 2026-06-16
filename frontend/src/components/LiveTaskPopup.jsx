/**
 * components/LiveTaskPopup.jsx — Iter 114
 *
 * Floating bottom-right card that shows real-time progress of a CTO task:
 *   §1 Live tape (steps[])
 *   §2 What changed (files_changed[])
 *   §3 Vanguard bugs (vanguard_findings[])
 *   §4 Bottom bar (commit, agent, tokens, time, view-on-github)
 *
 * BEHAVIOUR
 *   - Polls GET /api/aurem-dev/cto/tasks/{task_id} every 2s
 *   - On status=done → display result → vanish in 5s
 *   - On status=failed → stay until user closes
 *   - taskId === null → unmount immediately (new chat dismiss path)
 *   - Only ONE popup ever mounted at a time (caller enforces)
 */
import React, { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

const POLL_MS    = 2000;
const DISMISS_MS = 5000;

// Iter 168 — phase chip config. Phases come from `step.kind`
// (`phase_read` / `phase_think` / `phase_write` / `phase_verify` /
// `phase_commit`) emitted by the backend worker, plus a terminal
// `done` derived from task status.
const PHASES = {
  phase_read:   { icon: "📡", label: "Reading repo",    color: "#60a5fa", pulse: true  },
  phase_think:  { icon: "🧠", label: "Thinking",        color: "#c084fc", pulse: true  },
  phase_write:  { icon: "✏️", label: "Writing",         color: "#f59e0b", pulse: true  },
  phase_verify: { icon: "🛡️", label: "Security check",  color: "#34d399", pulse: false },
  phase_commit: { icon: "🚀", label: "Committing",      color: "#22c55e", pulse: false },
  done:         { icon: "✅", label: "Done",            color: "#22c55e", pulse: false },
};

const C = {
  amber:  "#c8922a",
  green:  "#6dd4a1",
  red:    "#ff6b6b",
  bg:     "#0c0f15",
  border: "#22262f",
  dim:    "#7a7e88",
  ink:    "#e8e2cf",
};

export default function LiveTaskPopup({ taskId, onClose }) {
  const [task, setTask] = useState(null);
  const [err,  setErr]  = useState("");
  const dismissTimerRef = useRef(null);
  const pollTimerRef    = useRef(null);

  // unmount whenever taskId changes / becomes null (new chat dismiss)
  // Iter 151 — same terminal-state widening + retry cap as in
  // MessageBubble. Previously only `done` / `failed` stopped polling;
  // `error`, `blocked`, `cancelled` etc. spun forever.
  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;
    let attempts = 0;
    const MAX_ATTEMPTS = 900;                       // 900 * POLL_MS ≈ 30 min @ 2 s
    const TERMINAL = new Set([
      "done", "failed", "error", "blocked", "rejected",
      "cancelled", "canceled", "completed", "timed_out",
    ]);
    const tick = async () => {
      if (cancelled) return;
      attempts += 1;
      if (attempts > MAX_ATTEMPTS) return;          // hard cap
      try {
        const r = await api.get(`/cto/tasks/${taskId}`);
        const t = r?.data?.task;
        if (!cancelled && t) {
          setTask(t);
          if (t.status === "done") {
            if (!dismissTimerRef.current) {
              dismissTimerRef.current = setTimeout(() => {
                if (!cancelled) onClose?.();
              }, DISMISS_MS);
            }
            return;
          }
          if (TERMINAL.has(t.status)) return;       // any other terminal → stop
        }
      } catch (e) {
        if (!cancelled) setErr(e?.response?.data?.detail || e.message || String(e));
        // 4xx → permission/missing; never retry. 5xx/network → 2 retries then bail.
        const code = e?.response?.status;
        if (code && code >= 400 && code < 500) return;
        if (attempts >= 3) return;
      }
      pollTimerRef.current = setTimeout(tick, POLL_MS);
    };
    tick();

    return () => {
      cancelled = true;
      if (pollTimerRef.current)    clearTimeout(pollTimerRef.current);
      if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
    };
  }, [taskId, onClose]);

  if (!taskId) return null;

  const status  = task?.status || "starting";
  const steps   = task?.steps || [];
  const changes = task?.files_changed || [];
  const findings = task?.vanguard_findings || [];
  const reads   = task?.files_read || [];
  const sha     = task?.commit_sha;
  const ghUrl   = task?.github_url;
  const agent   = task?.agent_used || "DeepSeek";
  const tokens  = task?.tokens_used;
  const elapsed = task?.time_taken_seconds;

  // Iter 168 — derive phase chip history from steps[].
  // Each step persists a `kind` ∈ PHASES when the worker tagged it
  // (or our heuristic classifier in _log inferred one). We collapse
  // consecutive same-kind steps and cap the chip strip at 5 entries.
  const phaseHistory = [];
  for (const s of steps) {
    const k = s.kind;
    if (!k || !PHASES[k]) continue;
    if (phaseHistory[phaseHistory.length - 1]?.kind === k) continue;
    phaseHistory.push({ kind: k, step: s.step, ts: s.ts });
  }
  if (status === "done" && phaseHistory[phaseHistory.length - 1]?.kind !== "done") {
    phaseHistory.push({ kind: "done", step: "Done", ts: Date.now() / 1000 });
  }
  const trimmedHistory = phaseHistory.slice(-5);
  const activePhase = status === "done" || status === "failed"
    ? null
    : trimmedHistory[trimmedHistory.length - 1] || null;

  // Extract the file currently being written (for the highlight line).
  let writingFile = null;
  if (activePhase?.kind === "phase_write") {
    // Walk steps from the end, find latest "💾 path" or "✏️ X files".
    for (let i = steps.length - 1; i >= 0; i--) {
      const text = steps[i].step || "";
      const m = text.match(/💾\s+([^\s]+)/);
      if (m) { writingFile = m[1].split("/").pop(); break; }
    }
  }

  const statusColor = status === "done"   ? C.green
                    : status === "failed" ? C.red
                    : C.amber;

  return (
    <div
      data-testid="live-task-popup"
      style={{
        position: "fixed",
        right: 16,
        top: "50%",
        transform: "translateY(-50%)",
        width: "min(360px, calc(100vw - 32px))",
        maxHeight: "70vh",
        minHeight: 80,
        background: "rgba(10, 12, 20, 0.72)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        color: C.ink,
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: 12,
        boxShadow:
          "0 8px 32px rgba(0,0,0,0.4), " +
          "inset 0 1px 0 rgba(255,255,255,0.06)",
        overflowY: "auto",
        overflowX: "hidden",
        fontFamily: "ui-monospace, Menlo, monospace",
        fontSize: 12,
        zIndex: 7500,
        animation: "popup-slide-in 0.25s ease-out",
        pointerEvents: "auto",
      }}>
      <style>{`
        @keyframes popup-slide-in {
          from {
            opacity: 0;
            transform: translateY(-50%) translateX(20px);
          }
          to {
            opacity: 1;
            transform: translateY(-50%) translateX(0);
          }
        }
        @keyframes phase-pulse {
          0%, 100% { opacity: 1;   transform: scale(1);   }
          50%      { opacity: 0.4; transform: scale(1.3); }
        }
      `}</style>

      {/* Header */}
      <div style={{
        padding: "9px 12px", borderBottom: `1px solid ${C.border}`,
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            width: 7, height: 7, borderRadius: 99,
            background: statusColor,
            boxShadow: status !== "done" && status !== "failed"
              ? `0 0 8px ${statusColor}` : "none",
          }}/>
          <span style={{ color: statusColor, fontWeight: 600 }}>
            {status === "done"     ? "✓ ORA done"
            : status === "failed"  ? "✗ ORA failed"
            :                        "⚡ ORA working…"}
          </span>
        </div>
        <button
          data-testid="ltp-close-btn"
          onClick={() => onClose?.()}
          aria-label="Close"
          style={{ background: "transparent", color: C.dim, border: "none",
                   cursor: "pointer", fontSize: 14, padding: 4 }}
        >✕</button>
      </div>

      {/* Iter 168 — Phase chip strip */}
      {trimmedHistory.length > 0 && (
        <div
          data-testid="ltp-phase-strip"
          style={{
            padding: "10px 12px",
            borderBottom: "1px solid rgba(255,255,255,0.06)",
            display: "flex", flexWrap: "wrap", gap: 4,
          }}
        >
          {trimmedHistory.map((p, i) => {
            const cfg = PHASES[p.kind] || {};
            const isActive = activePhase?.kind === p.kind &&
                             i === trimmedHistory.length - 1;
            return (
              <div
                key={`${p.kind}-${i}`}
                data-testid={`ltp-phase-chip-${p.kind}`}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 4,
                  padding: "3px 8px",
                  borderRadius: 12,
                  background: isActive
                    ? `${cfg.color}22`
                    : "rgba(255,255,255,0.04)",
                  border: `1px solid ${isActive
                    ? cfg.color + "44"
                    : "transparent"}`,
                  fontSize: 11,
                  color: isActive ? cfg.color : "#475569",
                  transition: "all 0.2s",
                  fontFamily:
                    "system-ui, -apple-system, sans-serif",
                }}
              >
                <span>{cfg.icon}</span>
                <span style={{ fontWeight: isActive ? 600 : 400 }}>
                  {cfg.label}
                  {isActive && writingFile && p.kind === "phase_write"
                    ? `: ${writingFile}`
                    : ""}
                </span>
                {isActive && cfg.pulse && (
                  <span
                    style={{
                      width: 5, height: 5,
                      borderRadius: "50%",
                      background: cfg.color,
                      animation: "phase-pulse 1s infinite",
                      display: "inline-block",
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Iter 168 — Currently writing file highlight */}
      {activePhase?.kind === "phase_write" && writingFile && (
        <div
          data-testid="ltp-writing-file"
          style={{
            padding: "6px 14px",
            background: "rgba(245,158,11,0.06)",
            borderBottom: "1px solid rgba(245,158,11,0.1)",
            display: "flex", alignItems: "center", gap: 8,
          }}
        >
          <span
            style={{
              width: 6, height: 6,
              borderRadius: "50%",
              background: "#f59e0b",
              animation: "phase-pulse 0.8s infinite",
              flexShrink: 0,
            }}
          />
          <span style={{
            fontSize: 11,
            fontFamily: "monospace",
            color: "#f59e0b",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1, minWidth: 0,
          }}>
            {writingFile}
          </span>
          <span style={{ fontSize: 10, color: "#64748b", flexShrink: 0 }}>
            being written…
          </span>
        </div>
      )}

      {err && (
        <div data-testid="ltp-error" style={{ padding: 10, color: C.red, fontSize: 11 }}>
          {String(err).slice(0, 200)}
        </div>
      )}

      {/* §1 Live tape */}
      <section data-testid="ltp-livetape" style={sec()}>
        {reads.length > 0 && (
          <Row icon="✅" text={
            `Read ${reads.length} files · ${reads.reduce((a,r) => a + (r.lines_count || 0), 0)} lines`
          } sub={reads.map(r => `${r.name} (${r.lines_count})`).join(" · ")}/>
        )}
        {steps.slice(-6).map((s, i) => (
          <Row key={i}
               icon={iconFor(s.status)}
               text={s.step || s.label || JSON.stringify(s).slice(0, 80)}
               testid={`ltp-step-${i}`}/>
        ))}
      </section>

      {/* §2 What changed */}
      {changes.length > 0 && (
        <section data-testid="ltp-changes" style={sec()}>
          <H>What changed</H>
          {changes.map((c, i) => (
            <div key={i} data-testid={`ltp-change-${i}`}
                 style={{ marginTop: 6, paddingLeft: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={{ color: C.ink }}>{c.name}</span>
                <span style={{ color: C.dim }}>
                  <span style={{ color: C.green }}>+{c.lines_added || 0}</span>
                  {" "}
                  <span style={{ color: C.red }}>-{c.lines_removed || 0}</span>
                  {c.line_number ? `  L${c.line_number}` : ""}
                </span>
              </div>
              {c.old_value != null && (
                <div style={{ color: C.red, paddingLeft: 12, whiteSpace: "pre-wrap",
                              wordBreak: "break-all" }}>- {c.old_value}</div>
              )}
              {c.new_value != null && (
                <div style={{ color: C.green, paddingLeft: 12, whiteSpace: "pre-wrap",
                              wordBreak: "break-all" }}>+ {c.new_value}</div>
              )}
            </div>
          ))}
        </section>
      )}

      {/* §3 Vanguard */}
      <section data-testid="ltp-vanguard" style={sec()}>
        {findings.length === 0 ? (
          <Row icon="🛡" text="Vanguard — clean (25 patterns checked)" color={C.green}/>
        ) : (
          <>
            <Row icon="🛡" text={`Vanguard blocked ${findings.length} issue${findings.length !== 1 ? "s" : ""}:`} color={C.red}/>
            {findings.slice(0, 5).map((f, i) => (
              <div key={i} data-testid={`ltp-finding-${i}`}
                   style={{ paddingLeft: 18, marginTop: 3 }}>
                <span style={{ color: f.severity === "CRITICAL" ? C.red : C.amber }}>
                  {f.severity}
                </span>{" "}
                <span style={{ color: C.ink }}>{f.rule}</span>
                <span style={{ color: C.dim }}>
                  {f.file ? ` · ${f.file}` : ""}{f.line ? `:${f.line}` : ""}
                </span>
                {f.status === "fixed" && (
                  <span style={{ color: C.green }}> · ✓ fixed</span>
                )}
              </div>
            ))}
          </>
        )}
      </section>

      {/* §4 Bottom bar */}
      {sha && (
        <section data-testid="ltp-bottom" style={{
          padding: "9px 12px", borderTop: `1px solid ${C.border}`,
          display: "flex", flexDirection: "column", gap: 4,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span data-testid="ltp-commit-sha"
                  style={{ color: C.green }}>✓ {String(sha).slice(0, 7)}</span>
            <span style={{ color: C.dim }}>
              {agent}{tokens ? ` · ${formatTokens(tokens)} tokens` : ""}{elapsed != null ? ` · ${elapsed}s` : ""}
            </span>
          </div>
          {ghUrl && (
            <a data-testid="ltp-view-github" href={ghUrl}
               target="_blank" rel="noopener noreferrer"
               style={{ color: C.amber, fontSize: 11, textDecoration: "underline" }}>
              View on GitHub →
            </a>
          )}
        </section>
      )}
    </div>
  );
}

const sec = () => ({
  padding: "9px 12px",
  borderTop: `1px solid ${C.border}`,
});

function H({ children }) {
  return <div style={{ color: C.dim, fontSize: 10, letterSpacing: ".08em",
                       textTransform: "uppercase", marginBottom: 2 }}>{children}</div>;
}

function Row({ icon, text, sub, color, testid }) {
  return (
    <div data-testid={testid} style={{ marginTop: 3 }}>
      <div style={{ color: color || C.ink, display: "flex", gap: 6 }}>
        <span>{icon}</span><span>{text}</span>
      </div>
      {sub && (
        <div style={{ color: C.dim, paddingLeft: 18, marginTop: 2,
                      wordBreak: "break-all", whiteSpace: "normal" }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function iconFor(status) {
  if (status === "success" || status === "ok"     || status === "done")    return "✅";
  if (status === "error"   || status === "failed" || status === "blocked") return "❌";
  if (status === "warning" || status === "warn")                            return "⚠️";
  return "⚡";
}

function formatTokens(n) {
  if (!n) return "0";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
