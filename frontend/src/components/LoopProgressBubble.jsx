/**
 * LoopProgressBubble.jsx — Iter 331 · collapsible loop-progress chat
 * bubble (founder-reported: loop step lines rendered as an ever-
 * growing flat list in the transcript; finished runs should collapse
 * to a one-line summary and expand on click).
 *
 * Detection is content-based (`**Step N / 5 —` lines from
 * ChatPanel.renderEventLine) so PERSISTED history turns from before
 * this iteration collapse too — not just live `loopLive` messages.
 *
 * Behaviour:
 *   - streaming (live run)  → expanded, header shows "Running…"
 *   - terminal / historical → collapsed by default, click to expand
 */
import React, { useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";

export function isLoopProgressContent(text) {
  if (!text) return false;
  return /\*\*Step \d \/ 5 —/.test(text)
      || /^Plan ready — awaiting approval/m.test(text);
}

function summarize(text, streaming) {
  const stepCount = (text.match(/\*\*Step \d \/ 5 —/g) || []).length;
  if (/\*\*Aborted\*\*/.test(text))
    return { stepCount, status: "Aborted", color: "var(--text-faint)" };
  if (/\*\*Failed\*\*/.test(text))
    return { stepCount, status: "Failed", color: "var(--danger, #ef4444)" };
  if (/ship complete|shipped/i.test(text))
    return { stepCount, status: "Shipped", color: "var(--ok, #22c55e)" };
  if (streaming)
    return { stepCount, status: "Running…", color: "var(--accent-2, #e8a020)" };
  return { stepCount, status: "Finished", color: "var(--text-dim)" };
}

export default function LoopProgressBubble({ text, streaming, children }) {
  const [open, setOpen] = useState(false);
  const expanded = streaming || open;
  const { stepCount, status, color } = summarize(text || "", streaming);
  return (
    <div data-testid="loop-progress-bubble" data-expanded={expanded ? "true" : "false"}>
      <button
        type="button"
        data-testid="loop-progress-toggle"
        onClick={() => setOpen((v) => !v)}
        disabled={streaming}
        aria-expanded={expanded}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          width: "100%", textAlign: "left",
          background: "var(--panel-2, rgba(255,255,255,0.03))",
          border: "1px solid var(--border, rgba(255,255,255,0.08))",
          borderRadius: 8, padding: "7px 10px",
          color: "var(--text-dim)", cursor: streaming ? "default" : "pointer",
          fontSize: 12,
          fontFamily: "ui-monospace, SFMono-Regular, JetBrains Mono, monospace",
        }}
      >
        {expanded
          ? <ChevronDown size={13} style={{ flexShrink: 0 }} />
          : <ChevronRight size={13} style={{ flexShrink: 0 }} />}
        <span data-testid="loop-progress-count">
          Loop run · {stepCount} step event{stepCount === 1 ? "" : "s"}
        </span>
        <span
          data-testid="loop-progress-status"
          style={{ marginLeft: "auto", color, fontWeight: 600 }}
        >
          {status}
        </span>
      </button>
      {expanded && (
        <div data-testid="loop-progress-body" style={{ marginTop: 8 }}>
          {children}
        </div>
      )}
    </div>
  );
}
