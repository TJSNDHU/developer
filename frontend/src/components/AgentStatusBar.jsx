/**
 * AgentStatusBar.jsx — Iter 295 (Frontend Layer 1, Batch 1)
 *
 * Extracted from ChatPanel.jsx (iter284/288) so it's testable in
 * isolation. Behaviour unchanged — the JSX was previously inlined
 * inside ChatPanel; this component takes exactly the two props the
 * inline JSX read (busy, queuedCount) and returns null when !busy.
 *
 * The prop-only interface + null-return-on-!busy is what iter288's
 * bug required: the bar MUST vanish the instant `busy` flips false
 * on a terminal event. LoopStepBar-style tests can now query
 * `screen.queryByTestId("agent-status-bar")` before and after a
 * simulated terminal event.
 */
import React from "react";

export default function AgentStatusBar({ busy, queuedCount = 0 }) {
  if (!busy) return null;
  return (
    <div className="chat-inline-card" data-testid="agent-status-shell">
      <div
        data-testid="agent-status-bar"
        style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "8px 14px",
          margin: "0 0 -1px 0",   // sits flush against the composer
          border: "1px solid rgba(255,102,8,0.35)",
          borderBottom: "none",
          borderRadius: "12px 12px 0 0",
          background: "rgba(255,102,8,0.06)",
          fontFamily: "'JetBrains Mono', ui-monospace, monospace",
          fontSize: 11.5,
          color: "#d8dade",
        }}
      >
        {queuedCount > 0 && (
          <span
            data-testid="queued-chip"
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "3px 10px", borderRadius: 999,
              background: "rgba(255,102,8,0.14)",
              border: "1px solid rgba(255,102,8,0.35)",
              color: "#FF8A3D", fontWeight: 600,
              fontSize: 10.5, letterSpacing: ".04em",
            }}
          >
            ▸ {queuedCount} queued
          </span>
        )}
        <span style={{
          display: "inline-flex", alignItems: "center", gap: 8,
          color: "#FF8A3D", fontWeight: 600,
        }}>
          <span style={{
            width: 8, height: 8, borderRadius: "50%",
            background: "#FF6608",
            boxShadow: "0 0 8px #FF660888",
            animation: "agent-pulse 1.4s ease-in-out infinite",
          }} />
          Agent is running…
        </span>
        <style>{`
          @keyframes agent-pulse {
            0%,100% { opacity: 1;   transform: scale(1);   }
            50%     { opacity: 0.5; transform: scale(1.25); }
          }
          form.glass-composer[data-agent-running="true"] {
            border-color: rgba(255,102,8,0.35) !important;
            border-top-left-radius: 0 !important;
            border-top-right-radius: 0 !important;
          }
        `}</style>
      </div>
    </div>
  );
}
