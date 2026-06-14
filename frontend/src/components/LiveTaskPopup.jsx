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

  const statusColor = status === "done"   ? C.green
                    : status === "failed" ? C.red
                    : C.amber;

  return (
    <div
      data-testid="live-task-popup"
      style={{
        position: "fixed", right: 16, bottom: 16,
        width: 360, maxHeight: 480, overflow: "auto",
        background: C.bg, color: C.ink,
        border: `1px solid ${C.border}`,
        borderRadius: 10,
        boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
        fontFamily: "ui-monospace, Menlo, monospace",
        fontSize: 12, zIndex: 9999,
        animation: "ltp-slide-in 220ms ease-out",
      }}>
      <style>{`
        @keyframes ltp-slide-in {
          from { transform: translateX(20px); opacity: 0; }
          to   { transform: translateX(0);    opacity: 1; }
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
