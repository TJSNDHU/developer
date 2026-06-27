/**
 * LoopStepBar.jsx — Iter 212m-58
 *
 * Horizontal 5-segment progress strip that visualises ORA's
 * pipeline phases while running in Loop mode:
 *
 *   1 Plan       — ORA drafts a plan; user must approve.
 *   2 Execute    — ORA writes files one at a time.
 *   3 Verify     — Ruff / ESLint on each file (max 3 retries).
 *   4 Security   — Vanguard / Shield scan runs automatically.
 *   5 Ship       — Commit (only after all prior steps pass).
 *
 * Driven entirely by the `phase` prop:
 *   • 'idle'                 — bar hidden
 *   • 'plan_pending'         — Step 1 pulsing amber (awaiting approval)
 *   • 'plan_approved'        — Step 1 green, Step 2 starts
 *   • 'executing'            — Step 2 pulsing
 *   • 'verifying'            — Step 3 pulsing (retry counter visible)
 *   • 'security'             — Step 4 pulsing
 *   • 'shipping'             — Step 5 pulsing
 *   • 'done'                 — all green
 *   • 'error'                — current step red, downstream greyed
 *
 * `retryCount` (0-3) and `errorStep` (1-5) are optional context props.
 */
import React from "react";
import { CheckCircle2, Circle, AlertTriangle, Loader2 } from "lucide-react";

const STEPS = [
  { id: 1, key: "plan",     label: "Plan" },
  { id: 2, key: "execute",  label: "Execute" },
  { id: 3, key: "verify",   label: "Verify" },
  { id: 4, key: "security", label: "Security" },
  { id: 5, key: "ship",     label: "Ship" },
];

// Map phase → the step index currently active (1-5).
const PHASE_TO_STEP = {
  idle:           0,
  plan_pending:   1,
  plan_approved:  1,
  executing:      2,
  verifying:      3,
  security:       4,
  shipping:       5,
  done:           5,
  error:          0,   // errorStep prop drives this
};

export default function LoopStepBar({ phase, retryCount = 0, errorStep = 0 }) {
  if (!phase || phase === "idle") return null;
  const active = phase === "error" ? errorStep : (PHASE_TO_STEP[phase] || 0);
  const isDone = phase === "done";

  return (
    <div
      data-testid="loop-step-bar"
      data-phase={phase}
      role="status"
      aria-label={`Loop step ${active} of 5`}
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "8px 12px", margin: "0 12px 6px",
        background: "var(--surface-2, rgba(255,255,255,0.03))",
        border: "1px solid var(--border, rgba(255,255,255,0.10))",
        borderRadius: 8,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 10.5,
        flexWrap: "wrap",
      }}
    >
      {STEPS.map((s, i) => {
        const done = isDone || s.id < active;
        const live = !isDone && s.id === active && phase !== "error";
        const errd = phase === "error" && s.id === errorStep;
        const future = !done && !live && !errd;
        return (
          <React.Fragment key={s.id}>
            <span
              data-testid={`loop-step-${s.key}`}
              data-step-state={errd ? "error" : done ? "done" : live ? "active" : "future"}
              style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                color: errd ? "#f87171"
                  : done ? "#86efac"
                  : live ? "#c4b5fd"
                  : "var(--text-dim, #9aa3b2)",
                opacity: future ? 0.55 : 1,
              }}
            >
              {errd
                ? <AlertTriangle size={11} />
                : done
                  ? <CheckCircle2 size={11} />
                  : live
                    ? <Loader2 size={11} className="anim-spin" />
                    : <Circle size={10} />}
              <span style={{ fontWeight: live ? 600 : 500 }}>
                {s.id}. {s.label}
              </span>
            </span>
            {i < STEPS.length - 1 && (
              <span
                aria-hidden
                style={{
                  flex: "0 0 8px", height: 1,
                  background: done
                    ? "rgba(134,239,172,0.45)"
                    : "var(--border, rgba(255,255,255,0.10))",
                }}
              />
            )}
          </React.Fragment>
        );
      })}
      <span
        data-testid="loop-retry-pill"
        style={{
          marginLeft: "auto",
          padding: "2px 8px",
          borderRadius: 999,
          fontSize: 9.5, letterSpacing: 0.3,
          color: retryCount > 0 ? "#fdba74" : "var(--text-dim, #9aa3b2)",
          background: retryCount > 0 ? "rgba(249,115,22,0.10)" : "transparent",
          border: `1px solid ${retryCount > 0
            ? "rgba(249,115,22,0.4)"
            : "var(--border, rgba(255,255,255,0.10))"}`,
        }}
      >
        max 3 retries{retryCount > 0 ? ` • ${retryCount}/3 used` : ""}
      </span>
      <style>{`
        .anim-spin { animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
