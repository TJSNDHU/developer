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
 * Iter 212m-130 — `locked` prop. When true (non-founder accounts),
 * the toggle renders a disabled "LOOP — COMING SOON" pill with a
 * gold lock badge. Clicks do NOT toggle; they fire an
 * `aurem:loop-coming-soon` event that the parent can surface as a
 * toast.  Loop Mode is being hardened (stuck-in-loop + verify
 * retry storms reported in production) — the founder unlocks it
 * for themselves to debug.
 *
 * Lives INSIDE the composer toolbar (next to the Paperclip / Github
 * icons) — no longer above the composer.
 */
import React from "react";
import { RefreshCw, Lock } from "lucide-react";

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

export default function LoopModeToggle({ value, onChange, locked = false }) {
  const isLoop = value === EXEC_MODES.LOOP;

  // Iter 212m-130 — Locked / Coming Soon variant. Non-founder users
  // see this pill until the engine is hardened. We render it as a
  // pseudo-button so screen-readers still announce the disabled
  // state + the click handler fires a global event the toast layer
  // listens to.
  if (locked) {
    return (
      <button
        type="button"
        data-testid="loop-mode-toggle-locked"
        data-locked="1"
        aria-disabled="true"
        aria-pressed="false"
        onClick={() => {
          try {
            window.dispatchEvent(new CustomEvent("aurem:loop-coming-soon"));
          } catch { /* ignore */ }
        }}
        title="Loop Mode — coming soon (in hardening)"
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "7px 14px",
          borderRadius: 999,
          border: "1px dashed rgba(251,191,36,0.45)",
          background: "rgba(251,191,36,0.06)",
          color: "rgba(251,191,36,0.85)",
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 11, fontWeight: 700, letterSpacing: "0.08em",
          textTransform: "uppercase",
          cursor: "not-allowed",
          whiteSpace: "nowrap",
          userSelect: "none",
          opacity: 0.85,
          transition: "background 140ms",
        }}
      >
        <Lock size={11} strokeWidth={2.5} />
        Loop · soon
      </button>
    );
  }

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
