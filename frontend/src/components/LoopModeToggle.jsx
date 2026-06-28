/**
 * LoopModeToggle.jsx — Iter 212m-103 (v0 pixel-perfect rewrite)
 *
 * Chunky "LOOP ON / LOOP OFF" pill button matching the screenshot
 * spec. Single click toggles between:
 *
 *   • PROMPT — one-shot reply (LOOP OFF, dark pill)
 *   • LOOP   — 5-phase Plan → Execute → Verify → Scan → Ship (LOOP ON,
 *              bright orange pill)
 *
 * Lives INSIDE the composer toolbar (next to the Paperclip / Github
 * icons) — no longer above the composer.
 */
import React from "react";
import { RefreshCw } from "lucide-react";

export const EXEC_MODE_KEY = "ora_execution_mode";
export const EXEC_MODES = { PROMPT: "prompt", LOOP: "loop" };

export function loadExecMode() {
  try {
    const v = localStorage.getItem(EXEC_MODE_KEY);
    return v === EXEC_MODES.LOOP ? EXEC_MODES.LOOP : EXEC_MODES.PROMPT;
  } catch {
    return EXEC_MODES.PROMPT;
  }
}

export function saveExecMode(m) {
  try { localStorage.setItem(EXEC_MODE_KEY, m); } catch { /* ignore */ }
}

export default function LoopModeToggle({ value, onChange }) {
  const isLoop = value === EXEC_MODES.LOOP;
  const flip = () => {
    const next = isLoop ? EXEC_MODES.PROMPT : EXEC_MODES.LOOP;
    saveExecMode(next);
    onChange?.(next);
  };
  return (
    <button
      type="button"
      data-testid="loop-mode-toggle"
      data-loop-active={isLoop ? "1" : "0"}
      onClick={flip}
      aria-pressed={isLoop}
      title={isLoop ? "Loop mode ON — click to switch to Prompt mode" : "Click to enable Loop mode (Plan → Execute → Verify → Scan → Ship)"}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "7px 14px",
        borderRadius: 999,
        border: isLoop ? "1px solid #FF6608" : "1px solid rgba(255,255,255,0.12)",
        background: isLoop ? "#FF6608" : "transparent",
        color: isLoop ? "#0A0A0A" : "rgba(255,255,255,0.72)",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11, fontWeight: 700, letterSpacing: "0.08em",
        textTransform: "uppercase",
        cursor: "pointer",
        boxShadow: isLoop ? "0 0 18px -4px rgba(255,102,8,0.55)" : "none",
        transition: "background 140ms, color 140ms, border-color 140ms, box-shadow 200ms",
        whiteSpace: "nowrap",
        userSelect: "none",
      }}
    >
      <RefreshCw size={12} strokeWidth={2.5} />
      {isLoop ? "Loop on" : "Loop off"}
    </button>
  );
}
