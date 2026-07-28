/**
 * LoopActionCards.jsx — Iter 212m-63 (Loop Mode Phase D lite)
 *
 * Two small components for the live-loop UX:
 *
 *   <SelfHealIndicator>
 *     Slim inline strip that lights up while the engine is rewriting
 *     a file that failed ruff/eslint.  Animates a self-heal "spinning
 *     wrench" + "attempt n/3" copy.  Hidden when state isn't
 *     self-healing.
 *
 *   <UserActionCard>
 *     Renders when a phase pauses and demands explicit input
 *     (`requires_user_action: true` from the SSE event schema).
 *     Three primary actions — Try Again / Skip / Abort — plus an
 *     optional feedback note the engine forwards as
 *     /pause-response.feedback.
 *
 * Both components are pure render — the parent (ChatPanel) owns the
 * state machine + API calls.
 */
import React, { useState } from "react";
import { Wrench, Play, SkipForward, X, AlertTriangle, ShieldCheck, Rocket } from "lucide-react";


export function SelfHealIndicator({ visible, attempt = 1, max = 3,
                                    errorPreview }) {
  if (!visible) return null;
  return (
    <div
      data-testid="self-heal-indicator"
      role="status"
      aria-live="polite"
      style={{
        display: "flex", alignItems: "center", gap: 10,
        margin: "8px 12px",
        padding: "8px 14px",
        borderRadius: 10,
        background: "linear-gradient(90deg, rgba(168,85,247,0.10), rgba(99,102,241,0.06))",
        border: "1px solid rgba(168,85,247,0.40)",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
        color: "#c4b5fd",
      }}
    >
      <Wrench size={13} className="anim-spin" />
      <span style={{ flex: 1, minWidth: 0 }}>
        Self-heal — attempt <strong>{attempt}/{max}</strong>: ORA is
        rewriting failing file{errorPreview ? "…" : ""}
      </span>
      {errorPreview && (
        <code style={{
          fontSize: 9.5,
          color: "#a78bfa",
          padding: "2px 6px",
          background: "rgba(168,85,247,0.10)",
          borderRadius: 4,
          maxWidth: 280,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>{errorPreview}</code>
      )}
      <style>{`
        .anim-spin { animation: spin 1.2s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}


/**
 * @param {object} props
 * @param {string} props.phase   — phase the loop paused on (verify/scan/etc.)
 * @param {string} props.message — the engine's human-readable reason
 * @param {string[]} [props.errors] — optional flattened error lines
 * @param {(action: 'retry'|'skip'|'abort', feedback?: string) => void} props.onAction
 */
export function UserActionCard({ phase, message, errors,
                                  onAction, busy,
                                  gateType, testsTouched }) {
  const [feedback, setFeedback] = useState("");
  // Iter 332 — dedicated SHIP human-review gate. Test files were
  // modified, so the engine paused for explicit approval. Generic
  // retry/skip buttons here soft-locked the engine; the ONLY valid
  // actions are Approve & Ship or Cancel.
  if (gateType === "ship_human_review") {
    return (
      <div
        data-testid="ship-review-gate-card"
        data-phase={phase}
        role="region"
        aria-label="Human review required before ship"
        style={{
          margin: "10px 12px",
          padding: 14,
          background: "linear-gradient(135deg, rgba(34,197,94,0.10), rgba(16,185,129,0.05))",
          border: "1px solid rgba(34,197,94,0.40)",
          borderRadius: 12,
          display: "flex", flexDirection: "column", gap: 10,
          fontFamily: "'JetBrains Mono', monospace",
          boxShadow: "0 0 28px -10px rgba(34,197,94,0.45)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <ShieldCheck size={14} color="#86efac" />
          <strong style={{ fontSize: 12, color: "#86efac", letterSpacing: 0.4 }}>
            Human review required — test files modified
          </strong>
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text, #e8ecf3)", lineHeight: 1.5 }}>
          {message}
        </div>
        {Array.isArray(testsTouched) && testsTouched.length > 0 && (
          <pre
            data-testid="ship-review-tests-touched"
            style={{
              margin: 0, padding: "8px 10px",
              background: "rgba(0,0,0,0.40)",
              border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: 6,
              fontSize: 10.5, color: "#86efac",
              maxHeight: 120, overflowY: "auto",
              whiteSpace: "pre-wrap", wordBreak: "break-all",
            }}
          >
            {testsTouched.slice(0, 10).join("\n")}
            {testsTouched.length > 10 && `\n…and ${testsTouched.length - 10} more`}
          </pre>
        )}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <ActionBtn
            testid="loop-approve-ship-btn"
            Icon={Rocket} label="Approve & Ship"
            tone="primary" disabled={busy}
            onClick={() => onAction?.("approve_ship")}
          />
          <ActionBtn
            testid="loop-cancel-ship-btn"
            Icon={X} label="Cancel ship"
            tone="danger" disabled={busy}
            onClick={() => onAction?.("cancel_ship")}
          />
        </div>
      </div>
    );
  }
  return (
    <div
      data-testid="user-action-card"
      data-phase={phase}
      role="region"
      aria-label="Loop paused — your input needed"
      style={{
        margin: "10px 12px",
        padding: 14,
        background: "linear-gradient(135deg, rgba(244,63,94,0.10), rgba(244,114,182,0.05))",
        border: "1px solid rgba(244,63,94,0.40)",
        borderRadius: 12,
        display: "flex", flexDirection: "column", gap: 10,
        fontFamily: "'JetBrains Mono', monospace",
        boxShadow: "0 0 28px -10px rgba(244,63,94,0.45)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <AlertTriangle size={14} color="#fda4af" />
        <strong style={{ fontSize: 12, color: "#fda4af", letterSpacing: 0.4 }}>
          Loop paused at <code style={{ color: "#fff" }}>{phase}</code> — your input needed
        </strong>
      </div>
      <div style={{
        fontSize: 11.5, color: "var(--text, #e8ecf3)", lineHeight: 1.5,
      }}>
        {message}
      </div>
      {Array.isArray(errors) && errors.length > 0 && (
        <pre
          data-testid="user-action-errors"
          style={{
            margin: 0, padding: "8px 10px",
            background: "rgba(0,0,0,0.40)",
            border: "1px solid rgba(255,255,255,0.06)",
            borderRadius: 6,
            fontSize: 10.5, color: "#fda4af",
            maxHeight: 140, overflowY: "auto",
            whiteSpace: "pre-wrap", wordBreak: "break-all",
          }}
        >
          {errors.slice(0, 12).join("\n")}
          {errors.length > 12 && `\n…and ${errors.length - 12} more`}
        </pre>
      )}
      <textarea
        data-testid="user-action-feedback"
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
        placeholder="Optional note for ORA (e.g. 'try a different library')"
        disabled={busy}
        style={{
          width: "100%", minHeight: 52,
          padding: "8px 10px",
          background: "var(--surface-2, rgba(255,255,255,0.04))",
          border: "1px solid var(--border, rgba(255,255,255,0.12))",
          borderRadius: 8,
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 11, color: "var(--text, #e8ecf3)",
          resize: "vertical",
        }}
      />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <ActionBtn
          testid="loop-retry-btn"
          Icon={Play} label="Try a different approach"
          tone="primary" disabled={busy}
          onClick={() => onAction?.("retry", feedback || undefined)}
        />
        <ActionBtn
          testid="loop-skip-btn"
          Icon={SkipForward} label="Skip this step"
          tone="warn" disabled={busy}
          onClick={() => onAction?.("skip", feedback || undefined)}
        />
        <ActionBtn
          testid="loop-abort-btn"
          Icon={X} label="Abort loop"
          tone="danger" disabled={busy}
          onClick={() => onAction?.("abort", feedback || undefined)}
        />
      </div>
    </div>
  );
}


function ActionBtn({ testid, Icon, label, tone, disabled, onClick }) {
  const colors = {
    primary: { bg: "linear-gradient(135deg, #22c55e, #16a34a)",
               color: "#0a0a0a", border: "transparent",
               shadow: "0 6px 16px -8px rgba(34,197,94,0.6)" },
    warn:    { bg: "rgba(249,115,22,0.10)",
               color: "#fdba74",
               border: "rgba(249,115,22,0.45)",
               shadow: "none" },
    danger:  { bg: "transparent",
               color: "#fda4af",
               border: "rgba(244,63,94,0.45)",
               shadow: "none" },
  }[tone] || { bg: "transparent", color: "#fff",
               border: "rgba(255,255,255,0.12)", shadow: "none" };
  return (
    <button
      type="button"
      data-testid={testid}
      disabled={disabled}
      onClick={onClick}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "7px 14px",
        background: colors.bg,
        color: colors.color,
        border: `1px solid ${colors.border}`,
        borderRadius: 8,
        fontSize: 11.5, fontWeight: 700,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.55 : 1,
        boxShadow: colors.shadow,
      }}
    >
      <Icon size={12} />
      {label}
    </button>
  );
}
