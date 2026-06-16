/**
 * components/WarmStatusBar.jsx — Iter 165
 *
 * Thin amber strip that sits at the top of the chat-messages area and
 * shows the 4 warm-start agents' progress. Renders nothing when status
 * is "idle" or "ready" so users only see it during the ~2-6s window
 * between project select and first chat turn.
 */
import React from "react";

export default function WarmStatusBar({ status, progress }) {
  if (status === "idle" || status === "ready") return null;

  const pct = Math.max(0, Math.min(1, Number(progress) || 0));
  const label =
    status === "warming"
      ? `Loading your project… ${Math.round(pct * 100)}%`
      : "Ready";

  return (
    <div
      data-testid="warm-status-bar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "6px 16px",
        background: "rgba(245,158,11,0.06)",
        borderBottom: "1px solid rgba(245,158,11,0.18)",
        fontSize: 11,
        color: "var(--text-faint)",
        letterSpacing: 0.2,
      }}
    >
      <div
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: "#f59e0b",
          animation: "warmPulse 1s infinite",
          flexShrink: 0,
        }}
      />
      <span style={{ flexShrink: 0 }}>{label}</span>
      <div
        style={{
          flex: 1,
          height: 2,
          background: "var(--border)",
          borderRadius: 1,
          overflow: "hidden",
        }}
      >
        <div
          data-testid="warm-progress-fill"
          style={{
            width: `${pct * 100}%`,
            height: "100%",
            background: "#f59e0b",
            transition: "width 0.3s ease",
            borderRadius: 1,
          }}
        />
      </div>
      <style>{`
        @keyframes warmPulse {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}
