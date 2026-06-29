/**
 * components/chat/ToolButton.jsx — Iter 212m-153
 *
 * Small icon button used in the chat composer toolbar (Attach,
 * GitHub, etc.).  Extracted from ChatPanel.jsx so the button is
 * trivially reusable in other places that mount a chat-style
 * toolbar (composer fork, mini-chat dock, etc.).
 *
 * Props:
 *   testid    HTML data-testid for the testing agent.
 *   title     Native browser tooltip.
 *   onClick   Click handler.
 *   Icon      lucide-react Icon component.
 *   active    boolean — true → accent fill + glow.
 *   className optional extra class.
 *   wide      boolean — Iter 154 — 34×34 → 42×34 so the Attach +
 *             GitHub buttons read clearer after the Maxx retire.
 */
import React from "react";

export default function ToolButton({
  testid, title, onClick, Icon, active, className, wide,
}) {
  const w = wide ? 42 : 34;
  return (
    <button
      type="button"
      data-testid={testid}
      title={title}
      onClick={onClick}
      className={className}
      style={{
        width: w, height: 34, borderRadius: wide ? 8 : 4,
        background: active ? "var(--accent-soft)" : "transparent",
        border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
        color: active ? "var(--accent-2)" : "var(--text-dim)",
        cursor: "pointer",
        display: "flex", alignItems: "center", justifyContent: "center",
        transition: "color 120ms, border-color 120ms, background 120ms, box-shadow 220ms",
        boxShadow: active ? "0 0 14px -3px var(--accent)" : "none",
      }}
      onMouseEnter={(e) => {
        if (!active) {
          e.currentTarget.style.color = "var(--accent-2)";
          e.currentTarget.style.borderColor = "var(--border-strong)";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          e.currentTarget.style.color = "var(--text-dim)";
          e.currentTarget.style.borderColor = "var(--border)";
        }
      }}
    >
      <Icon size={wide ? 15 : 14} />
    </button>
  );
}
