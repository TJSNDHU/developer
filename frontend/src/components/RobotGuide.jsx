/**
 * RobotGuide.jsx — ORA mascot guide card.
 *
 * Reusable across NewUserWizard, Projects "Connect a repo" modal,
 * Login, Signup, and any future onboarding surface that needs a
 * friendly contextual hint.
 *
 * Usage:
 *   import RobotGuide, { RobotGuideKeyframes } from "../components/RobotGuide";
 *
 *   <RobotGuideKeyframes />   // mount once near the root of your subtree
 *   <RobotGuide message="<strong>Welcome!</strong> Click <strong>Continue with GitHub</strong> <span class='ora-arrow'>👇</span>" />
 *
 * Props:
 *   - message  : HTML string (rendered via dangerouslySetInnerHTML). Use
 *                `<strong>`, `<em>`, and `<span class="ora-arrow">…</span>`
 *                for the bouncing emoji arrow.
 *   - kind     : "info" (default amber) | "error" (red "HEADS UP")
 *                | "success" (green "ALL SET")
 *   - testid   : optional override for the root data-testid.
 */
import React from "react";

export default function RobotGuide({ message, kind = "info", testid = "robot-guide" }) {
  const palette = paletteFor(kind);
  return (
    <div data-testid={testid} style={{
      background: palette.bg,
      border: `1px solid ${palette.border}`,
      borderRadius: 12, padding: "12px 14px", marginBottom: 16,
      display: "flex", gap: 12, alignItems: "flex-start",
      transition: "all .3s ease",
    }}>
      <div data-testid={`${testid}-face`} style={{
        width: 36, height: 36,
        background: palette.face,
        borderRadius: 8, position: "relative", flexShrink: 0,
      }}>
        {/* eyes */}
        <div style={{ position:"absolute", top:9, left:7, width:7, height:7,
                       background:"#000", borderRadius:"50%",
                       animation:"oraBlink 3s infinite" }} />
        <div style={{ position:"absolute", top:9, right:7, width:7, height:7,
                       background:"#000", borderRadius:"50%",
                       animation:"oraBlink 3s infinite 0.1s" }} />
        {/* mouth */}
        <div style={{ position:"absolute", bottom:7, left:"50%",
                       transform:"translateX(-50%)", width:14, height:4,
                       background:"#000", borderRadius:2 }} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 10, color: palette.label,
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          letterSpacing: "0.08em", marginBottom: 4,
        }}>
          {palette.labelText}
        </div>
        <div data-testid={`${testid}-msg`}
             style={{ fontSize: 13, color: "#f8fafc", lineHeight: 1.55 }}
             // eslint-disable-next-line react/no-danger
             dangerouslySetInnerHTML={{ __html: message }} />
      </div>
    </div>
  );
}

function paletteFor(kind) {
  if (kind === "error") {
    return {
      bg: "rgba(255,107,107,0.06)",
      border: "rgba(255,107,107,0.3)",
      face: "#ef4444",
      label: "#ef4444",
      labelText: "ORA · HEADS UP",
    };
  }
  if (kind === "success") {
    return {
      bg: "rgba(34,197,94,0.07)",
      border: "rgba(34,197,94,0.3)",
      face: "#22c55e",
      label: "#22c55e",
      labelText: "ORA · ALL SET",
    };
  }
  return {
    bg: "rgba(245,158,11,0.06)",
    border: "rgba(245,158,11,0.25)",
    face: "#f59e0b",
    label: "#f59e0b",
    labelText: "ORA GUIDE",
  };
}

/** Escape a string for safe embedding into the `message` HTML prop. */
export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** Mount once near the root of any tree that uses <RobotGuide />. */
export function RobotGuideKeyframes() {
  return <style>{KEYFRAMES}</style>;
}

const KEYFRAMES = `
@keyframes oraBlink {
  0%,90%,100% { transform: scaleY(1); }
  95% { transform: scaleY(0.1); }
}
@keyframes oraPulseRing {
  0% { opacity: 1; transform: scale(1); }
  100% { opacity: 0; transform: scale(1.08); }
}
@keyframes oraBounce {
  0%,100% { transform: translateY(0); }
  50% { transform: translateY(-3px); }
}
.ora-arrow { display: inline-block; animation: oraBounce 1s infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
`;

/** Inline style for a pulsing amber ring around a primary CTA. */
export const oraPulseRingStyle = {
  position: "absolute", inset: -4, borderRadius: 12,
  border: "2px solid #f59e0b", pointerEvents: "none",
  animation: "oraPulseRing 1.5s infinite",
};
