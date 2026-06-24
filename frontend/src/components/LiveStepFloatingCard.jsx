/**
 * LiveStepFloatingCard.jsx — Iter 212m-19
 *
 * Floating progress card pinned to the right edge of the chat panel
 * while ORA is actively processing a turn. Driven by the SAME SSE
 * `{type:"step", text, done}` events that feed <StepCards/>, plus the
 * `meta` frame (provider + thinking_s + tool_calls_run) for the
 * model-name footer.
 *
 * Layout:
 *   ┌─────────────────────────────────────────────────┐
 *   │  [🤔 Thinking] [📖 Reading repo] [✍️ Writing]    │  ← phase pills
 *   │  [🚀 Committing] [✅ Done]                       │
 *   ├─────────────────────────────────────────────────┤
 *   │  › 🤔 Thinking…                                 │
 *   │  › 📖 Reading repo…                             │  ← step log
 *   │  › ✍️ Writing files…                            │
 *   ├─────────────────────────────────────────────────┤
 *   │  glm-5.2  ·  4.5k tokens                         │  ← footer
 *   └─────────────────────────────────────────────────┘
 *
 * Behaviour:
 *   - Active phase pill is highlighted; completed ones dimmed
 *   - Step log shows ALL steps received this turn, newest at bottom
 *   - When `done === true` the card stays for 3s, then auto-closes
 *   - Closes immediately if the user dismisses the message thread
 */
import React, { useEffect, useState } from "react";

const PHASES = [
  { id: "thinking",   label: "🤔 Thinking" },
  { id: "reading",    label: "📖 Reading repo" },
  { id: "writing",    label: "✍️ Writing" },
  { id: "committing", label: "🚀 Committing" },
  { id: "done",       label: "✅ Done" },
];

/**
 * Map a step.text emoji prefix → phase id. Backend canonicalises step
 * text via _STEP_LABELS (orchestrator.py) so the leading emoji is the
 * stable phase signal.
 */
function phaseFor(text) {
  if (!text) return "thinking";
  if (text.startsWith("✅")) return "done";
  if (text.startsWith("🚀")) return "committing";
  if (text.startsWith("✍️")) return "writing";
  if (text.startsWith("📖")) return "reading";
  if (text.startsWith("🔍")) return "thinking";   // Claude review pass
  if (text.startsWith("⚙️")) return "thinking";   // Generic tool / fallback
  return "thinking";
}


/**
 * @param {{
 *   steps:    Array<{text: string, done: boolean, ts: number}>,
 *   provider: string|null,
 *   tokens:   number,
 *   onClose:  () => void,
 * }} props
 */
export default function LiveStepFloatingCard({ steps, provider, tokens, onClose }) {
  const [closing, setClosing] = useState(false);

  // Identify the active phase + completed phases.
  const lastStep = steps && steps.length ? steps[steps.length - 1] : null;
  const activePhase = lastStep ? phaseFor(lastStep.text) : "thinking";
  const isDone = !!(lastStep && lastStep.done);
  // Phases that have appeared at least once in this stream.
  const seenPhases = new Set();
  for (const s of steps || []) seenPhases.add(phaseFor(s.text));

  // Iter 212m-19 — auto-close 3s after done=true so the user can savour
  // the ✅ Done frame before the card slides out.
  useEffect(() => {
    if (!isDone) return undefined;
    const t1 = setTimeout(() => setClosing(true), 2400);
    const t2 = setTimeout(() => onClose?.(),     3000);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [isDone, onClose]);

  if (!steps || steps.length === 0) return null;

  return (
    <div
      data-testid="live-step-floating-card"
      data-done={isDone ? "true" : "false"}
      style={{
        position: "absolute",
        top: 24,
        right: 24,
        width: 340,
        zIndex: 6,
        background: "rgba(13,16,24,0.86)",
        border: "1px solid rgba(255,200,120,0.16)",
        borderRadius: 10,
        backdropFilter: "blur(16px) saturate(140%)",
        WebkitBackdropFilter: "blur(16px) saturate(140%)",
        boxShadow: "0 12px 28px rgba(0,0,0,0.42)",
        overflow: "hidden",
        opacity: closing ? 0 : 1,
        transform: closing ? "translateY(-8px)" : "translateY(0)",
        transition: "opacity 380ms ease, transform 380ms ease",
        pointerEvents: closing ? "none" : "auto",
      }}
    >
      {/* ── Phase pills ───────────────────────────────────── */}
      <div
        data-testid="live-step-phases"
        style={{
          display: "flex", flexWrap: "wrap", gap: 6,
          padding: "10px 12px 8px 12px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        {PHASES.map((p) => {
          const active = activePhase === p.id;
          const seen   = seenPhases.has(p.id);
          return (
            <span
              key={p.id}
              data-testid={`live-step-pill-${p.id}`}
              data-active={active ? "true" : "false"}
              style={{
                fontSize: 10,
                padding: "3px 7px",
                borderRadius: 4,
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: ".02em",
                background: active
                  ? "rgba(255,197,96,0.18)"
                  : seen
                  ? "rgba(109,212,161,0.10)"
                  : "rgba(255,255,255,0.04)",
                color: active
                  ? "#FFD58A"
                  : seen
                  ? "#6DD4A1"
                  : "rgba(255,255,255,0.4)",
                border: active
                  ? "1px solid rgba(255,197,96,0.36)"
                  : "1px solid transparent",
                whiteSpace: "nowrap",
              }}
            >
              {p.label}
            </span>
          );
        })}
      </div>

      {/* ── Step log ──────────────────────────────────────── */}
      <div
        data-testid="live-step-log"
        style={{
          maxHeight: 200,
          overflowY: "auto",
          padding: "8px 12px",
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 11.5,
        }}
      >
        {steps.map((s, idx) => {
          const isLast = idx === steps.length - 1;
          const inProgress = isLast && !s.done && !isDone;
          return (
            <div
              key={`${s.ts}-${idx}`}
              style={{
                color: inProgress ? "#FFD58A" : "rgba(255,255,255,0.66)",
                opacity: inProgress ? 1 : 0.85,
                padding: "2px 0",
                lineHeight: 1.4,
              }}
            >
              <span style={{ color: "rgba(255,255,255,0.32)", marginRight: 6 }}>›</span>
              {s.text}
            </div>
          );
        })}
      </div>

      {/* ── Footer: model + tokens ────────────────────────── */}
      {(provider || tokens) && (
        <div
          data-testid="live-step-footer"
          style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "6px 12px",
            borderTop: "1px solid rgba(255,255,255,0.06)",
            background: "rgba(0,0,0,0.25)",
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 10,
            color: "rgba(255,255,255,0.5)",
          }}
        >
          <span data-testid="live-step-model">{provider || "—"}</span>
          {typeof tokens === "number" && tokens > 0 && (
            <span data-testid="live-step-tokens">
              {tokens >= 1000
                ? `${(tokens / 1000).toFixed(1)}k tokens`
                : `${tokens} tokens`}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
