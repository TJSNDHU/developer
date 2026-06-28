/**
 * LoopStepBar.jsx — Iter 212m-93 (v0 pill format)
 *
 * Horizontal pill row that visualises ORA's pipeline phases. Matches
 * sidebar-changes.vercel.app exactly:
 *   [LOOP] [✓ PLAN] [—] [⟳ EXECUTE] [○ VERIFY] [○ SCAN] [○ SHIP]
 *
 * Icons: ✓ for done, ⟳ (spinner) for active, ○ for pending, ⚠ for error.
 */
import React from "react";
import { Check, Circle, AlertTriangle, Loader2 } from "lucide-react";

const STEPS = [
  { id: 1, key: "plan",     label: "PLAN" },
  { id: 2, key: "execute",  label: "EXECUTE" },
  { id: 3, key: "verify",   label: "VERIFY" },
  { id: 4, key: "security", label: "SCAN" },
  { id: 5, key: "ship",     label: "SHIP" },
];

const PHASE_TO_STEP = {
  idle: 0, plan_pending: 1, plan_approved: 1,
  executing: 2, verifying: 3, security: 4,
  shipping: 5, done: 5, error: 0,
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
        display: "flex", alignItems: "center", gap: 6,
        padding: "8px 12px", margin: "0 12px 6px",
        background: "#161616",
        border: "1px solid #222",
        borderRadius: 8,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 10,
        flexWrap: "wrap",
        letterSpacing: "0.06em",
      }}
    >
      <span style={{
        padding: "2px 7px", borderRadius: 4,
        background: "rgba(255,102,8,0.12)",
        color: "#FF6608", fontWeight: 700,
        fontSize: 9.5,
      }}>LOOP</span>

      {STEPS.map((s, i) => {
        const done = isDone || s.id < active;
        const live = !isDone && s.id === active && phase !== "error";
        const errd = phase === "error" && s.id === errorStep;
        const future = !done && !live && !errd;
        const color = errd ? "#EF4444"
          : done ? "#22C55E"
          : live ? "#FF6608"
          : "#666";
        return (
          <React.Fragment key={s.id}>
            <span
              data-testid={`loop-step-${s.key}`}
              data-step-state={errd ? "error" : done ? "done" : live ? "active" : "future"}
              style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "2px 6px", borderRadius: 4,
                color, fontWeight: live ? 700 : 600,
                opacity: future ? 0.55 : 1,
                background: live ? "rgba(255,102,8,0.08)" : "transparent",
              }}
            >
              {errd
                ? <AlertTriangle size={10} />
                : done
                  ? <Check size={10} strokeWidth={3} />
                  : live
                    ? <Loader2 size={10} className="loop-spin" />
                    : <Circle size={9} />}
              <span>{s.label}</span>
            </span>
            {i < STEPS.length - 1 && (
              <span aria-hidden style={{
                color: "#333", fontSize: 9, userSelect: "none",
              }}>—</span>
            )}
          </React.Fragment>
        );
      })}

      {retryCount > 0 && (
        <span
          data-testid="loop-retry-pill"
          style={{
            marginLeft: "auto",
            padding: "2px 7px", borderRadius: 4,
            fontSize: 9, fontWeight: 600,
            color: "#FB923C",
            background: "rgba(251,146,60,0.10)",
            border: "1px solid rgba(251,146,60,0.32)",
          }}
        >{retryCount}/3 retries</span>
      )}

      <style>{`
        .loop-spin { animation: loop-spin 1s linear infinite; }
        @keyframes loop-spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

