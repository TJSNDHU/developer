/**
 * StepCards.jsx — Iter 212m-19
 *
 * Live progress cards rendered inside an ORA assistant bubble while
 * `/chat/stream` is producing SSE `{type:"step", text, done}` events
 * (Iter 212m-18). Each step the orchestrator fires (🤔 Thinking,
 * 📖 Reading repo, ✍️ Writing files, 🚀 Committing, ✅ Done) becomes a
 * stacked card so the user sees ORA's actual plan executing — not a
 * generic "thinking…" pill.
 *
 * Visual contract:
 *   - Completed steps show ✅ and stay visible
 *   - The MOST RECENT step (until `done=true`) shows ⏳ + animates
 *   - Cards stack top → bottom, no border-radius between them so they
 *     visually connect like terminal log lines
 *   - Monospace font for the text, dark card style matching ORA
 *     bubbles
 *   - On `done=true` the last step flips to ✅
 *
 * The component is intentionally pure — it never fetches; the parent
 * (ChatPanel.jsx via streamChat) pushes new steps onto the message's
 * `steps` array and re-renders.
 */
import React from "react";

/**
 * @param {{ steps: Array<{text: string, done: boolean, ts: number}>,
 *           streaming: boolean }} props
 */
export default function StepCards({ steps, streaming }) {
  if (!Array.isArray(steps) || steps.length === 0) return null;

  return (
    <div
      data-testid="step-cards"
      data-streaming={streaming ? "true" : "false"}
      style={{
        display: "flex",
        flexDirection: "column",
        marginTop: 8,
        marginBottom: 4,
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: 6,
        overflow: "hidden",
        background: "rgba(0,0,0,0.25)",
        backdropFilter: "blur(4px)",
        maxWidth: 460,
      }}
    >
      {steps.map((s, idx) => {
        const isLast = idx === steps.length - 1;
        // When the overall stream is still running and this is the
        // tail step (not flagged done), show the in-progress hourglass.
        // Everything older is implicitly complete.
        const isInProgress = streaming && isLast && !s.done;
        return (
          <div
            key={`${s.ts}-${idx}`}
            data-testid={`step-card-${idx}`}
            data-step-state={isInProgress ? "running" : "done"}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "8px 12px",
              borderBottom: idx < steps.length - 1
                ? "1px solid rgba(255,255,255,0.06)"
                : "none",
              background: isInProgress
                ? "rgba(255,197,96,0.04)"
                : "transparent",
              transition: "background 240ms ease",
            }}
          >
            <span
              aria-hidden="true"
              style={{
                width: 18,
                display: "inline-flex",
                justifyContent: "center",
                fontSize: 13,
                lineHeight: 1,
                // Subtle pulse on the running step.
                animation: isInProgress
                  ? "stepHourglassSpin 1.6s linear infinite"
                  : "none",
              }}
            >
              {isInProgress ? "⏳" : "✅"}
            </span>
            <span
              style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 12.5,
                color: isInProgress
                  ? "rgba(255,235,205,0.95)"
                  : "rgba(255,255,255,0.78)",
                letterSpacing: "0.01em",
              }}
            >
              {s.text}
            </span>
          </div>
        );
      })}
      {/* The keyframe lives inline so the component is self-contained
          and doesn't depend on tailwind / index.css edits. */}
      <style>{`
        @keyframes stepHourglassSpin {
          0%, 100% { transform: rotate(0deg); opacity: 0.85; }
          50%      { transform: rotate(180deg); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
