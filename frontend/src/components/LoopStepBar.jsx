/**
 * LoopStepBar.jsx — Iter 212m-103 (v0 pixel-perfect rewrite)
 *
 *   [LOOP]  ✓ PLAN  ─  ⟳ EXECUTE  ─  ○ VERIFY  ─  ○ SCAN  ─  ○ SHIP
 *
 * • Done   → green check + bright green label
 * • Active → orange spinning ring + bright orange label, slight halo
 * • Future → empty gray circle + muted label
 * • Error  → red triangle + red label
 *
 * Visual shell: 1px rounded card (12px radius), #161616 background,
 * #2A2A2A border. Sits between the Slow-response banner and the
 * composer card.
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
  // Iter 212m-195 — Persistent visibility to match the v2 mock.
  // Previously we returned null on `idle` so the bar only appeared
  // while a loop was mid-flight; that hid the "kaunsa phase abhi
  // hai" affordance from users. Now the bar is always visible: in
  // `idle` all five steps render as muted pending circles (matches
  // the LOOP · PLAN → EXECUTE → VERIFY → SCAN → SHIP strip from the
  // v2 preview). Only return null when the phase prop is genuinely
  // missing (component not wired up).
  if (!phase) return null;
  const active = phase === "error" ? errorStep : (PHASE_TO_STEP[phase] || 0);
  const isDone = phase === "done";
  const isIdle = phase === "idle";

  return (
    <div
      data-testid="loop-step-bar"
      data-phase={phase}
      role="status"
      aria-label={`Loop step ${active} of 5`}
      style={{
        display: "flex", alignItems: "center", gap: 14,
        padding: "12px 18px",
        // Iter 212m-195 — align to the composer's horizontal padding
        // (`.glass-composer { padding: 14px clamp(16px, 17.25%, 240px) }`)
        // so the loop bar sits INSIDE the same visual column as the
        // chat input instead of overflowing on both sides on wide
        // screens (was `margin: 8px 12px 8px` → looked ~450px wider
        // than the composer at 1400px viewport).
        margin: "8px clamp(16px, 17.25%, 240px)",
        background: "#161616",
        border: "1px solid #2A2A2A",
        borderRadius: 12,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
        letterSpacing: "0.10em",
        overflowX: "auto",
        whiteSpace: "nowrap",
      }}
    >
      <span style={{
        color: "#8A8A8A", fontWeight: 700, fontSize: 11,
        letterSpacing: "0.14em",
        flexShrink: 0,
      }}>LOOP</span>

      {STEPS.map((s, i) => {
        const done = isDone || s.id < active;
        const live = !isDone && !isIdle && s.id === active && phase !== "error";
        const errd = phase === "error" && s.id === errorStep;
        const future = isIdle || (!done && !live && !errd);
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
                display: "inline-flex", alignItems: "center", gap: 7,
                color, fontWeight: 700,
                opacity: future ? 0.55 : 1,
                flexShrink: 0,
              }}
            >
              <span style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 16, height: 16, borderRadius: 999,
                border: live
                  ? "1.5px solid #FF6608"
                  : done
                    ? "1.5px solid #22C55E"
                    : errd
                      ? "1.5px solid #EF4444"
                      : "1.5px solid #444",
                background: live ? "rgba(255,102,8,0.10)" : "transparent",
                flexShrink: 0,
              }}>
                {errd
                  ? <AlertTriangle size={10} strokeWidth={2.5} />
                  : done
                    ? <Check size={10} strokeWidth={3} />
                    : live
                      ? <Loader2 size={10} className="loop-spin" strokeWidth={2.5} />
                      : <Circle size={5} strokeWidth={0} fill="transparent" />}
              </span>
              <span>{s.label}</span>
            </span>
            {i < STEPS.length - 1 && (
              <span aria-hidden style={{
                color: "#3A3A3A", fontSize: 11, userSelect: "none",
                flexShrink: 0, fontWeight: 700,
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
            padding: "3px 9px", borderRadius: 999,
            fontSize: 10, fontWeight: 700,
            color: "#FB923C",
            background: "rgba(251,146,60,0.10)",
            border: "1px solid rgba(251,146,60,0.32)",
            flexShrink: 0,
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
