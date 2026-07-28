/**
 * LoopStepBar.jsx — Iter 309 · ECG strip rewrite
 *
 * Adds a per-step ECG strip (14px tall) beneath each of the 5 phase
 * labels, driven ONLY by real backend narration events (Item 6 SSE
 * frames with `data.type === "narration"`).
 *
 * VISUAL STATES per step:
 *   • future   — flat neutral line, no animation
 *   • active   — scrolling ECG waveform (SVG polyline + CSS translateX
 *                loop, ~1s cycle), amber tone. Animates only while a
 *                real pending narration on that step remains unpaired.
 *   • resolved — flat green (last narration for step was
 *                `tone: success`) or flat red (last was `tone: danger`
 *                or `tone: warning`-terminal). Persists — never
 *                re-animates.
 *
 * DERIVATION RULE (this is what makes it real, not mocked):
 *   The active/resolved state for each step is derived STRICTLY from
 *   the folded narration history that ChatPanel passes in as
 *   `stepTones`. Zero client-side simulation. If the stream drops and
 *   nothing arrives, the step-bar simply doesn't advance — that's the
 *   truth of what the backend has emitted.
 *
 * RECONNECT/REPLAY (Item 6 · Part 2 requirement):
 *   ChatPanel accumulates the folded narration history from all
 *   replayed events after a reconnect, then re-passes `stepTones`.
 *   Because we derive state from that history in an order-invariant
 *   way (last tone per step wins), reconnect never causes a
 *   resolved-green step to flicker back to "active".
 *
 * ACCESSIBILITY:
 *   Respects `prefers-reduced-motion: reduce` — swaps the scrolling
 *   waveform for a pulsing opacity dot on that step. Same information,
 *   no motion sickness.
 */
import React from "react";
import { Check, Circle, AlertTriangle, Loader2 } from "lucide-react";

const STEPS = [
  { id: 1, key: "plan",     label: "PLAN" },
  { id: 2, key: "execute",  label: "EXECUTE" },
  { id: 3, key: "verify",   label: "VERIFY" },
  { id: 4, key: "security", label: "SCAN", narrationKey: "scan" },
  { id: 5, key: "ship",     label: "SHIP" },
];

const PHASE_TO_STEP = {
  idle: 0,
  plan_pending: 1, plan_approved: 1, planning: 1, awaiting_confirmation: 1,
  executing: 2, self_healing: 2, paused_for_user: 2,
  verifying: 3,
  security: 4, scanning: 4,
  shipping: 5, done: 5, completed: 5, shipped: 5,
  error: 0, failed: 0, aborted: 0, expired: 0,
};

// Colours
const COL = {
  amber:   "#FF6608",
  green:   "#22C55E",
  red:     "#EF4444",
  neutral: "#3A3A3A",
  muted:   "#666",
};

// ── ECG strip ───────────────────────────────────────────────────────
// Single seamless-looping SVG. The polyline is one "beat" wide (64px);
// we render it twice back-to-back and translate the whole `<g>` from
// 0 → -64px over 1s so it appears infinite.
function ECGStrip({ variant, testid }) {
  // variant: "future" | "active" | "success" | "danger"
  const height = 14;
  const beatW  = 64;
  const isActive = variant === "active";

  const color =
    variant === "active"  ? COL.amber
    : variant === "success" ? COL.green
    : variant === "danger"  ? COL.red
    : COL.neutral;

  // ECG "beat" path — small bump then a spike then flat. Kept simple
  // so it reads cleanly at 14px height.
  const beat = `M 0 7 L 12 7 L 16 5 L 20 7 L 26 7 L 30 1 L 34 13 L 38 7 L 64 7`;

  // Reduced-motion fallback — a single pulsing dot centered in a
  // flat line. Same visual language for future/active/resolved but
  // no scrolling motion.
  const prefersReduced =
    typeof window !== "undefined" &&
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (isActive && prefersReduced) {
    return (
      <div
        data-testid={testid}
        data-variant="active-reduced"
        style={{
          height, width: "100%",
          position: "relative",
          background: "transparent",
        }}
      >
        {/* Flat baseline */}
        <div style={{
          position: "absolute", top: "50%", left: 0, right: 0,
          height: 1, background: color, opacity: 0.55,
        }} />
        {/* Pulsing dot */}
        <div
          style={{
            position: "absolute", top: "50%", left: "50%",
            transform: "translate(-50%, -50%)",
            width: 6, height: 6, borderRadius: "50%",
            background: color,
            animation: "ecg-reduced-pulse 1.4s ease-in-out infinite",
          }}
        />
      </div>
    );
  }

  return (
    <div
      data-testid={testid}
      data-variant={variant}
      style={{
        height, width: "100%",
        position: "relative", overflow: "hidden",
      }}
      aria-hidden
    >
      {isActive ? (
        <svg
          width="100%" height={height}
          viewBox={`0 0 ${beatW * 2} ${height}`}
          preserveAspectRatio="none"
          style={{ display: "block" }}
        >
          <g style={{ animation: "ecg-scroll 1s linear infinite" }}>
            <path d={beat}                     fill="none" stroke={color} strokeWidth="1.4" />
            <path d={beat} transform={`translate(${beatW},0)`}
                  fill="none" stroke={color} strokeWidth="1.4" />
          </g>
        </svg>
      ) : (
        // Flat line — future (neutral) OR resolved (green/red).
        <div style={{
          position: "absolute", top: "50%", left: 0, right: 0,
          height: 1.4, background: color,
          opacity: variant === "future" ? 0.55 : 1,
        }} />
      )}
    </div>
  );
}

/**
 * @param {object} props
 * @param {string} props.phase                — current SSE-derived phase
 * @param {number} props.retryCount           — self-heal retry pill
 * @param {number} props.errorStep            — 1-5, step that failed
 * @param {object} [props.stepTones]          — { plan, execute, verify, scan, ship } →
 *                                              "pending" | "success" | "warning" | "danger" | null
 *                                              Derived by ChatPanel from real narration events.
 *                                              null / missing key = future step (untouched).
 */
export default function LoopStepBar({
  phase, retryCount = 0, errorStep = 0, stepTones = {},
}) {
  if (!phase) return null;

  const isDone   = phase === "done" || phase === "completed" || phase === "shipped";
  const isError  = phase === "error" || phase === "failed"
                || phase === "aborted" || phase === "expired";
  const active   = isError ? errorStep : (PHASE_TO_STEP[phase] || 0);
  const isIdle   = phase === "idle";

  // Derive per-step ECG variant from real narration tones.
  // Priority (highest wins):
  //   0. ── Iter 323/329 · terminal-state pending resolver ──
  //      When the loop reaches a terminal-success phase
  //      (`completed` / `done` / `shipped`), every step whose
  //      narration tone is still "pending" is force-resolved to
  //      "success". Same class of fix as LoopLiveFeed's
  //      resolvePendingOnTerminal (Iter 329 · Fix B) — some phases'
  //      correlation_id resolver frames never arrive (SSE gap OR
  //      backend narration omission). We also keep the legacy
  //      isDone → SHIP=success override so an all-tones-empty
  //      terminal still shows SHIP green.
  //
  //      Founder-observed bug (Iter 329 · Fix C · Bug 2): PLAN ✓,
  //      EXECUTE (amber spinning), VERIFY ✓, SCAN ✓, SHIP ✓ on a
  //      real completed ship (commit 0b79db0). Logically
  //      impossible — SHIP done implies EXECUTE done. Root cause:
  //      execute's stepTones stayed at "pending" and the prior
  //      Rule 0 only covered step 5.
  //
  //      For terminal-failure (isError) with a still-pending tone
  //      on a step that ISN'T the errorStep, we resolve to
  //      "future"-ish (not "active") because we don't know the
  //      real outcome but the loop is over — a spinning ECG on a
  //      dead loop is the worst UX. Only the actual errorStep
  //      gets "danger".
  //   1. If stepTones[key] === "success" → resolved green.
  //   2. If stepTones[key] === "danger" → resolved red.
  //   3. If stepTones[key] === "warning" → resolved green (warning
  //      alone doesn't fail the step; it's an in-run soft warn).
  //   4. If stepTones[key] === "pending" → active amber ECG.
  //   5. Else fall back to legacy phase-based logic (in case the
  //      backend hasn't emitted narration for that step yet or the
  //      loop is running on a stale build without narration).
  function ecgVariant(step) {
    const key = step.narrationKey || step.key;
    const tone = stepTones[key];
    // Rule 0-a: terminal-success — resolve any still-pending tone
    // to success + keep the legacy SHIP override.
    if (isDone) {
      if (tone === "pending") return "success";
      if (step.id === 5)      return "success";
    }
    // Rule 0-b: terminal-failure — the actual error step is danger;
    // any OTHER step still stuck on "pending" resolves to "future"
    // (loop is dead, spinner is a lie). Other tones fall through
    // to their normal handling below.
    if (isError) {
      if (step.id === errorStep) return "danger";
      if (tone === "pending")    return "future";
    }
    if (tone === "success" || tone === "warning") return "success";
    if (tone === "danger") return "danger";
    if (tone === "pending") return "active";
    // Legacy fallback
    if (isError && step.id === errorStep) return "danger";
    if (isDone || step.id < active)       return "success";
    if (!isDone && !isIdle && step.id === active && !isError) return "active";
    return "future";
  }

  return (
    <div
      data-testid="loop-step-bar"
      data-phase={phase}
      role="status"
      aria-label={`Loop step ${active} of 5`}
      style={{
        display: "flex", flexDirection: "column", gap: 6,
        padding: "12px 18px",
        margin: "8px clamp(16px, 17.25%, 240px)",
        background: "#161616",
        border: "1px solid #2A2A2A",
        borderRadius: 12,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
        letterSpacing: "0.10em",
      }}
    >
      {/* Row 1 — Labels + icons. Iter 330 · alignment fix — matches
          Row 2's grid columns 1:1 so each step chip sits directly
          above its ECG segment, regardless of chat-input width. */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "auto repeat(5, 1fr)",
        gap: 8, alignItems: "center",
        columnGap: 8,
      }}>
        <span style={{
          color: "#8A8A8A", fontWeight: 700, fontSize: 11,
          letterSpacing: "0.14em", width: 34,
        }}>LOOP</span>

        {STEPS.map((s) => {
          const variant = ecgVariant(s);
          const done = variant === "success";
          const live = variant === "active";
          const errd = variant === "danger";
          const future = variant === "future";
          const color = errd ? COL.red
            : done ? COL.green
            : live ? COL.amber
            : COL.muted;
          return (
            <span
              key={s.id}
              data-testid={`loop-step-${s.key}`}
              data-step-state={errd ? "error" : done ? "done" : live ? "active" : "future"}
              style={{
                display: "inline-flex", alignItems: "center",
                justifyContent: "center",
                gap: 7,
                color, fontWeight: 700,
                opacity: future ? 0.55 : 1,
                minWidth: 0, // allow shrink inside grid cell
              }}
            >
              <span style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 16, height: 16, borderRadius: 999,
                border: live ? `1.5px solid ${COL.amber}`
                  : done ? `1.5px solid ${COL.green}`
                  : errd ? `1.5px solid ${COL.red}`
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
              <span style={{
                overflow: "hidden", textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}>{s.label}</span>
            </span>
          );
        })}
      </div>

      {retryCount > 0 && (
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <span
            data-testid="loop-retry-pill"
            style={{
              padding: "3px 9px", borderRadius: 999,
              fontSize: 10, fontWeight: 700,
              color: "#FB923C",
              background: "rgba(251,146,60,0.10)",
              border: "1px solid rgba(251,146,60,0.32)",
            }}
          >{retryCount}/3 retries</span>
        </div>
      )}

      {/* Row 2 — ECG strips per step, columns align 1:1 with the
          label row above. Uses the same 5-column grid + LOOP header
          spacer so the ECG under EXECUTE literally sits beneath the
          EXECUTE label. */}
      <div
        data-testid="loop-step-ecg-row"
        style={{
          display: "grid",
          // 5 equal step columns; the "LOOP" prefix + dashes align via
          // the same clamped margin above. Keep it uniform — one ECG
          // per phase, evenly spaced.
          gridTemplateColumns: "auto repeat(5, 1fr)",
          gap: 8, alignItems: "center",
        }}
      >
        {/* Spacer to visually align under the "LOOP" label */}
        <span aria-hidden style={{ width: 34 }} />
        {STEPS.map((s) => {
          const variant = ecgVariant(s);
          return (
            <ECGStrip
              key={s.id}
              variant={variant}
              testid={`loop-step-ecg-${s.key}`}
            />
          );
        })}
      </div>

      <style>{`
        .loop-spin { animation: loop-spin 1s linear infinite; }
        @keyframes loop-spin { to { transform: rotate(360deg); } }
        @keyframes ecg-scroll {
          from { transform: translateX(0); }
          to   { transform: translateX(-64px); }
        }
        @keyframes ecg-reduced-pulse {
          0%,100% { opacity: 1;   transform: translate(-50%,-50%) scale(1);   }
          50%     { opacity: 0.5; transform: translate(-50%,-50%) scale(1.25); }
        }
        @media (prefers-reduced-motion: reduce) {
          .loop-spin { animation: none; }
        }
      `}</style>
    </div>
  );
}
