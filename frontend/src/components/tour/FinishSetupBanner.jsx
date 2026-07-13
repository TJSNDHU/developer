/**
 * components/tour/FinishSetupBanner.jsx — Iter 212m-200
 *
 * Dashboard banner shown to users who signed up but haven't connected
 * any GitHub repo yet.  Clicking "Show me how" launches the in-place
 * ConnectRepoTour.  The email deep link (?tour=connect-repo) triggers
 * the tour directly, bypassing the banner.
 *
 * Props
 *   visible     boolean    Show the banner (parent computes from
 *                          connection_status / project list).
 *   onLaunch    () => void Start the tour.
 *   onDismiss   () => void Hide the banner for this session.
 */
import React from "react";

export default function FinishSetupBanner({ visible, onLaunch, onDismiss }) {
  if (!visible) return null;
  return (
    <div
      data-testid="finish-setup-banner"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        margin: "8px 12px 4px",
        padding: "10px 16px",
        background: "rgba(245,158,11,0.10)",
        border: "1px solid rgba(245,158,11,0.4)",
        borderRadius: 10,
        color: "#fbbf24",
        fontFamily: '"JetBrains Mono", monospace',
        fontSize: 12,
        letterSpacing: "0.03em",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 8, height: 8, borderRadius: "50%",
          background: "#f59e0b",
          boxShadow: "0 0 8px rgba(245,158,11,0.7)",
          animation: "fsbBlink 1.4s ease-in-out infinite",
        }}
      />
      <div style={{ flex: 1, color: "#f8fafc" }}>
        <b style={{ color: "#fbbf24" }}>Finish setup</b> — connect a GitHub repo so ORA can chat with your code, run scans, and ship PRs.
      </div>
      <button
        type="button"
        data-testid="finish-setup-launch"
        onClick={onLaunch}
        style={{
          padding: "6px 14px",
          background: "#f59e0b",
          color: "#000",
          border: "none",
          borderRadius: 7,
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: "0.06em",
          cursor: "pointer",
        }}
      >
        SHOW ME HOW →
      </button>
      <button
        type="button"
        data-testid="finish-setup-dismiss"
        onClick={onDismiss}
        title="Dismiss for this session"
        style={{
          padding: "6px 10px",
          background: "transparent",
          color: "#94a3b8",
          border: "1px solid #334155",
          borderRadius: 7,
          fontSize: 11,
          cursor: "pointer",
        }}
      >
        ✕
      </button>
      <style>{`@keyframes fsbBlink { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }`}</style>
    </div>
  );
}
