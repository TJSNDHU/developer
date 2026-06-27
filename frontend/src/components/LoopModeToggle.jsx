/**
 * LoopModeToggle.jsx — Iter 212m-58
 *
 * Two-segment switcher that lives directly above the composer and
 * toggles ORA's execution behaviour between:
 *
 *   • PROMPT — one-shot reply, fast, no extra verification.
 *   • LOOP   — 5-phase pipeline (Plan → Execute → Verify → Security
 *              → Ship) with an inline Plan-approval gate, automatic
 *              verify retries (max 3), and auto-Shield scan.
 *
 * Selection is persisted to `localStorage.ora_execution_mode` and the
 * `onChange(mode)` callback fires on every flip. The component is
 * purely presentational — orchestration / behaviour swaps live in
 * ChatPanel.jsx.
 */
import React from "react";
import { Zap, Repeat } from "lucide-react";

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
  const switchTo = (m) => {
    if (m === value) return;
    saveExecMode(m);
    onChange?.(m);
  };
  return (
    <div
      data-testid="exec-mode-toggle"
      role="tablist"
      aria-label="ORA execution mode"
      style={{
        display: "inline-flex", alignItems: "center", gap: 0,
        padding: 3, margin: "0 12px 8px",
        borderRadius: 999,
        background: "var(--surface-2, rgba(255,255,255,0.04))",
        border: "1px solid var(--border, rgba(255,255,255,0.10))",
        width: "fit-content",
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      <SegBtn
        testid="exec-mode-prompt"
        active={!isLoop}
        Icon={Zap}
        label="Prompt mode"
        onClick={() => switchTo(EXEC_MODES.PROMPT)}
      />
      <SegBtn
        testid="exec-mode-loop"
        active={isLoop}
        Icon={Repeat}
        label="Loop mode"
        accent
        onClick={() => switchTo(EXEC_MODES.LOOP)}
      />
    </div>
  );
}

function SegBtn({ testid, active, Icon, label, onClick, accent }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      data-testid={testid}
      onClick={onClick}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "5px 14px",
        borderRadius: 999, border: "none",
        background: active
          ? (accent
              ? "linear-gradient(90deg, rgba(168,85,247,0.20), rgba(99,102,241,0.20))"
              : "var(--surface-3, rgba(255,255,255,0.10))")
          : "transparent",
        color: active
          ? (accent ? "#c4b5fd" : "var(--text, #e8ecf3)")
          : "var(--text-dim, #9aa3b2)",
        fontSize: 11.5, fontWeight: 600, letterSpacing: 0.3,
        cursor: "pointer",
        boxShadow: active && accent
          ? "0 0 16px -6px rgba(168,85,247,0.55)"
          : "none",
        transition: "background 140ms, color 140ms, box-shadow 200ms",
      }}
    >
      <Icon size={12} />
      {label}
    </button>
  );
}
