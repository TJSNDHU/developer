/**
 * components/chat/StreamHealthPill.jsx — Iter 212m-153
 *
 * (Originally Iter 212m-57.)
 *
 * Tiny inline status pill that sits above the composer when the SSE
 * chat stream stalls.  Driven by `streamHealth` state in ChatPanel:
 *   • phase === 'slow'         → amber dot + "Slow response… {n}s of
 *                                 silence — will auto-retry in {m}s"
 *   • phase === 'reconnecting' → red dot + "Reconnecting…"
 *   • phase === 'idle'         → renders nothing (null)
 * No close button — auto-clears on next token / done / error / Stop.
 */
import React from "react";
import { Zap } from "lucide-react";

export default function StreamHealthPill({ state, onRetry }) {
  if (!state || state.phase === "idle") return null;
  const isReconnect = state.phase === "reconnecting";
  const accent = isReconnect ? "#EF4444" : "#FF6608";
  return (
    <div
      data-testid="chat-stream-health-pill"
      data-stream-phase={state.phase}
      role="status"
      aria-live="polite"
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "12px 16px",
        margin: "8px 12px 0",
        borderRadius: 12,
        background: isReconnect
          ? "rgba(239,68,68,0.07)"
          : "rgba(255,102,8,0.07)",
        border: `1px solid ${accent}`,
        color: "#E5E5E5",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12,
        animation: isReconnect ? "pillPulse 1.2s ease-in-out infinite" : "none",
      }}
    >
      <Zap size={14} strokeWidth={2.5} style={{ color: accent, flexShrink: 0 }} />
      <span style={{ flex: 1, minWidth: 0,
                     display: "inline-flex", alignItems: "baseline", gap: 6 }}>
        <strong style={{ color: accent, fontWeight: 700 }}>
          {isReconnect ? "Reconnecting" : "Slow response"}
        </strong>
        <span style={{ color: "#9AA3B2" }}>
          · {state.silentFor}s silent
          {!isReconnect && state.retryEtaSec != null && (
            <> · auto-retry in {state.retryEtaSec}s</>
          )}
        </span>
      </span>
      {onRetry && (
        <button
          type="button"
          data-testid="chat-stream-retry-now"
          onClick={onRetry}
          style={{
            background: "transparent", border: "none",
            color: "#E5E5E5", fontSize: 12, fontWeight: 600,
            cursor: "pointer", padding: "4px 8px",
            fontFamily: "inherit",
          }}
        >
          Retry now
        </button>
      )}
      <style>{`
        @keyframes pillPulse {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0.65; }
        }
      `}</style>
    </div>
  );
}
