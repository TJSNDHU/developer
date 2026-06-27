/**
 * PlanApprovalCard.jsx — Iter 212m-58
 *
 * Inline approval gate shown at the end of Loop-mode Step 1 (Plan).
 * ORA emits a plan-only response when the user sends in Loop mode;
 * the card renders directly below the assistant bubble carrying that
 * plan and asks the user to explicitly Approve before code touches
 * disk. Cancel discards the loop session.
 *
 * Pure presentational — orchestration (sending the "approved,
 * proceed" follow-up) lives in ChatPanel.jsx.
 */
import React from "react";
import { Check, X, Sparkles } from "lucide-react";

export default function PlanApprovalCard({ onApprove, onCancel, disabled }) {
  return (
    <div
      data-testid="plan-approval-card"
      role="region"
      aria-label="Loop plan approval"
      style={{
        margin: "10px 12px",
        padding: 14,
        background: "linear-gradient(135deg, rgba(168,85,247,0.10), rgba(99,102,241,0.06))",
        border: "1px solid rgba(168,85,247,0.35)",
        borderRadius: 12,
        display: "flex", flexDirection: "column", gap: 10,
        fontFamily: "'JetBrains Mono', monospace",
        boxShadow: "0 0 28px -10px rgba(168,85,247,0.45)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Sparkles size={14} color="#c4b5fd" />
        <strong style={{ fontSize: 12, color: "#c4b5fd", letterSpacing: 0.4 }}>
          Plan ready — your approval needed
        </strong>
      </div>
      <div style={{
        fontSize: 11.5, color: "var(--text-dim, #c2c9d6)", lineHeight: 1.55,
      }}>
        Review the plan above. ORA will only start writing files once you
        approve. You can cancel any time before Step 2 begins.
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          data-testid="plan-approve-btn"
          disabled={disabled}
          onClick={onApprove}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "7px 14px",
            background: disabled
              ? "rgba(134,239,172,0.18)"
              : "linear-gradient(135deg, #22c55e, #16a34a)",
            color: disabled ? "#86efac" : "#0a0a0a",
            border: "none", borderRadius: 8,
            fontSize: 11.5, fontWeight: 700,
            cursor: disabled ? "not-allowed" : "pointer",
            opacity: disabled ? 0.7 : 1,
            boxShadow: disabled ? "none" : "0 6px 16px -8px rgba(34,197,94,0.6)",
            transition: "transform 120ms ease",
          }}
          onMouseEnter={(e) => { if (!disabled) e.currentTarget.style.transform = "translateY(-1px)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.transform = "translateY(0)"; }}
        >
          <Check size={12} />
          Approve &amp; Run
        </button>
        <button
          type="button"
          data-testid="plan-cancel-btn"
          disabled={disabled}
          onClick={onCancel}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "7px 14px",
            background: "transparent",
            color: disabled ? "var(--text-dim, #9aa3b2)" : "#fda4af",
            border: `1px solid ${disabled
              ? "var(--border, rgba(255,255,255,0.12))"
              : "rgba(244,63,94,0.45)"}`,
            borderRadius: 8,
            fontSize: 11.5, fontWeight: 600,
            cursor: disabled ? "not-allowed" : "pointer",
          }}
        >
          <X size={12} />
          Cancel loop
        </button>
      </div>
    </div>
  );
}
