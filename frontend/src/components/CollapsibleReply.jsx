/**
 * CollapsibleReply.jsx — Iter 339d · founder request: older/long chat
 * replies collapse to a ONE-LINE preview; click to expand, click again
 * to collapse. Mirrors the LoopProgressBubble pattern (Iter 331).
 */
import React, { useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";

const PREVIEW_CHARS = 110;

// Long enough to be worth collapsing.
export function isCollapsibleReply(text) {
  if (!text) return false;
  return text.length > 280 || text.split("\n").length > 6;
}

function firstLinePreview(text) {
  const line = (text || "")
    .replace(/```[\s\S]*?```/g, " [code] ")
    .replace(/[#*_`>-]/g, "")
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l.length > 0) || "";
  return line.length > PREVIEW_CHARS
    ? `${line.slice(0, PREVIEW_CHARS)}…` : line;
}

export default function CollapsibleReply({ text, children }) {
  const [open, setOpen] = useState(false);
  const lines = (text || "").split("\n").length;
  if (!open) {
    return (
      <button
        type="button"
        data-testid="collapsed-reply-toggle"
        aria-expanded={false}
        onClick={() => setOpen(true)}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          width: "100%", textAlign: "left",
          background: "transparent", border: "none",
          padding: 0, margin: 0,
          color: "var(--text-dim)", cursor: "pointer",
          fontSize: 14, fontFamily: "inherit", lineHeight: 1.5,
        }}
      >
        <ChevronRight size={13} style={{ flexShrink: 0 }} />
        <span style={{
          overflow: "hidden", textOverflow: "ellipsis",
          whiteSpace: "nowrap", flex: 1, minWidth: 0,
        }}>
          {firstLinePreview(text)}
        </span>
        <span style={{
          flexShrink: 0, fontSize: 11, color: "var(--text-faint)",
        }}>
          {lines} line{lines === 1 ? "" : "s"}
        </span>
      </button>
    );
  }
  return (
    <div data-testid="expanded-reply" data-expanded="true">
      <button
        type="button"
        data-testid="expanded-reply-collapse"
        aria-expanded
        onClick={() => setOpen(false)}
        style={{
          display: "inline-flex", alignItems: "center", gap: 5,
          background: "transparent", border: "none",
          padding: 0, marginBottom: 6,
          color: "var(--text-faint)", cursor: "pointer",
          fontSize: 11, fontFamily: "inherit",
        }}
      >
        <ChevronDown size={12} /> Collapse
      </button>
      {children}
    </div>
  );
}
