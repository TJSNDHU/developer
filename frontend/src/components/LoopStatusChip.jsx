/**
 * components/LoopStatusChip.jsx — Iter 309 · Batch-2 aftermath
 *
 * Persistent, unambiguous "loop is running" chip anchored to the top
 * of the chat panel (sticky, always visible while a loop is live).
 *
 * WHY THIS EXISTS
 * ───────────────
 * On 2026-07-26 a diagnostic-looking F12Badge in the chat surface
 * mutated state (sent a chat turn) and desynced the running-loop UI
 * mid-live-test. Root cause: the loop-running signal was inferred from
 * client-side state (LoopLiveFeed's placeholder, LoopStepBar phase)
 * that could regress to "Waiting for plan approval" when a foreign
 * component reset local React state. This chip closes that gap by:
 *
 *   1. Reading the truth from the BACKEND (`GET /loop/active`) every
 *      10 s + on tab focus, so client-side state resets can never
 *      make it lie about whether a loop is running.
 *   2. Showing a SINGLE Stop button — outlined red, action-styled,
 *      visually distinct from any diagnostic badge. Includes a 4-s
 *      "click again to confirm" inline state (no blocking modal).
 *   3. Exposing the real backend `loop_id` (short + full-on-hover)
 *      so support/debugging never has to guess the id from a UI
 *      alias.
 *
 * SCOPE LIMITS (per founder directive)
 * ────────────────────────────────────
 *   • Zero backend logic changes (loop_engine.py untouched).
 *   • Uses the shared authenticated `api` client via loopApi helpers
 *     — no raw fetch, no localStorage-token juggling.
 *   • The Stop action calls the existing POST /loop/{id}/cancel,
 *     which already handles the zombie-session fallback path
 *     server-side (writes aborted state + terminal event + releases
 *     lock).
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { getActiveLoop, getLoopStatus, cancelLoop } from "../lib/loopApi";

const POLL_MS = 10_000;
const CONFIRM_WINDOW_MS = 4_000;
// ── Iter 323 · Bug B — post-terminal grace window ───────────────
// When getActiveLoop() transitions active → null (backend filters
// terminal loops out of /loop/active), keep the last-known snapshot
// on screen for this long so the founder sees a "SHIPPED · <sha>"
// terminal state before the chip unmounts. Live incident: the chip
// vanished the instant the loop went COMPLETED, leaving no top-of-
// panel confirmation.
const TERMINAL_GRACE_MS = 30_000;

const C = {
  bg:      "#111827",
  border:  "#1f2937",
  text:    "#e6ebf3",
  dim:     "#9aa0a8",
  green:   "#34d399",
  red:     "#f87171",
  amber:   "#f5a524",
  mono:    "ui-monospace, SFMono-Regular, Menlo, monospace",
};

// Backend enum → user-readable label.
const PHASE_LABEL = {
  planning:              "PLANNING",
  plan:                  "PLANNING",
  awaiting_confirmation: "AWAITING APPROVAL",
  executing:             "EXECUTING",
  execute:               "EXECUTING",
  verifying:             "VERIFYING",
  verify:                "VERIFYING",
  scanning:              "SECURITY SCAN",
  scan:                  "SECURITY SCAN",
  security:              "SECURITY SCAN",
  shipping:              "SHIPPING",
  ship:                  "SHIPPING",
  paused_for_user:       "PAUSED · YOUR INPUT",
  self_healing:          "SELF-HEALING",
  // ── Iter 323 · Bug B — terminal labels ─────────────────────
  // Renders as the visible pill during the TERMINAL_GRACE_MS
  // grace window after a loop completes. Without these entries
  // the chip would fall through to the raw enum uppercased
  // ("COMPLETED" / "DONE") which the founder called out as
  // ambiguous ("shipped" is the user's mental model here).
  completed:             "SHIPPED",
  done:                  "SHIPPED",
  shipped:               "SHIPPED",
  // ── Iter 325 · terminal FAILURE labels ────────────────────
  // Failed / aborted / expired loops must show the FAILURE
  // reason at the top of the pane, NOT be silently labelled
  // SHIPPED (Iter 323's initial fix's bug). Founder screenshot:
  // a failed EXECUTE was visually rendered as "still running"
  // because the chip either vanished or wore the wrong label.
  failed:                "FAILED",
  aborted:               "ABORTED",
  expired:               "EXPIRED",
};

// ── Iter 329 · Fix C · Bug 1 — terminal-state label wins over phase ─
// When the loop is done, `state` is the source of truth for the pill
// label — NOT the last mid-loop `phase`. Prior code let phase win
// (phase || state), so a terminal snapshot with state=completed +
// phase="ship" mapped through PHASE_LABEL["ship"]="SHIPPING", freezing
// the chip on SHIPPING for the entire 30s terminal grace window
// instead of transitioning to SHIPPED.
//
// Same class as LoopLiveFeed's resolvePendingOnTerminal (Iter 329 ·
// Fix B): once the loop is terminal, no lingering mid-loop label can
// be legitimately in-progress. Terminal state MUST win.
const TERMINAL_STATES = new Set([
  "completed", "done", "shipped",
  "failed", "aborted", "expired", "cancelled", "canceled",
]);

export function phaseText(active) {
  if (!active) return "IDLE";
  const state = String(active.state || "").toLowerCase();
  const phase = String(active.phase || "").toLowerCase();
  // Rule 0 (Iter 329 · Fix C): terminal state trumps phase.
  if (TERMINAL_STATES.has(state)) {
    return PHASE_LABEL[state] || state.toUpperCase();
  }
  // Iter 312 · Class 3 companion — state-first for approval variants.
  // When the engine sits at state='awaiting_confirmation' during the
  // plan phase, the raw phase is still 'plan' (which maps to
  // 'PLANNING'). That produced a chip↔chat contradiction: chat
  // shows PlanApprovalCard while chip says PLANNING. Prefer state
  // whenever it's an approval-gate variant so the chip agrees with
  // the visible card. For all other running phases the finer-grained
  // phase field still wins (executing/verifying/shipping etc.).
  if (state === "awaiting_confirmation" || state === "paused_for_user") {
    const key = state;
    return PHASE_LABEL[key] || key.toUpperCase() || "RUNNING";
  }
  const key = phase || state || "";
  return PHASE_LABEL[key] || key.toUpperCase() || "RUNNING";
}

// Exposed for unit tests — pure helper, no state, no side effects.
export const __testables__ = { phaseText };

export default function LoopStatusChip({ projectId = null, onPhaseUpdate = null }) {
  const [active, setActive] = useState(null);
  const [err, setErr]       = useState(null);
  const [busy, setBusy]     = useState(false);
  // ── Iter 323 · Bug B — post-terminal grace window ─────────────
  // `terminalSnapshot` holds the last non-terminal `active` value
  // that was on screen when the poll returned null. The chip
  // renders this snapshot (with phase re-labelled to SHIPPED) for
  // TERMINAL_GRACE_MS before finally unmounting. Prevents the
  // vanish-the-instant-you-ship UX (live incident: commit 7bb304d).
  const [terminalSnapshot, setTerminalSnapshot] = useState(null);
  const lastActiveRef = useRef(null);
  const terminalTimerRef = useRef(null);
  // Confirm-again state: "idle" | "confirming" | "stopping" | "stopped"
  const [stopState, setStopState] = useState("idle");
  const confirmTimerRef = useRef(null);

  const poll = useCallback(async () => {
    try {
      const data = await getActiveLoop(projectId);
      const nextActive = data?.active || null;
      // ── Iter 325 · Terminal snapshot with ACTUAL state ───────
      // Iter 323 first pass set every terminal snapshot to
      // {state:"completed", phase:"shipped"} — wrong for FAILED /
      // ABORTED loops (chip rendered SHIPPED for a failed run).
      // Now: on active → null, fetch /loop/{id}/status to learn
      // the true terminal state, then set snapshot accordingly.
      // The fetch is best-effort — if it fails, fall back to the
      // "generic terminal" label so the chip still stays mounted.
      if (!nextActive && lastActiveRef.current) {
        const prev = lastActiveRef.current;
        const snap = { ...prev };
        let terminalKind = "shipped";        // default
        try {
          const term = await getLoopStatus(prev.loop_id);
          const trueState = String(term?.state || "").toLowerCase();
          if (trueState === "failed")   terminalKind = "failed";
          if (trueState === "aborted")  terminalKind = "aborted";
          if (trueState === "expired")  terminalKind = "expired";
          // completed → default "shipped"
          snap.state = trueState || "completed";
          // Keep the phase from the terminal doc when present so
          // the chip can show where it died (e.g. FAILED · EXECUTE).
          if (term?.phase) snap.phase = String(term.phase).toLowerCase();
          else             snap.phase = terminalKind;
          snap.commit = term?.context?.commit || null;
        } catch {
          // Best-effort — if status probe fails, treat as generic
          // terminal (defaults to shipped) rather than blocking UI.
          snap.state = "completed";
          snap.phase = "shipped";
        }
        setTerminalSnapshot(snap);
        if (terminalTimerRef.current) clearTimeout(terminalTimerRef.current);
        terminalTimerRef.current = setTimeout(() => {
          setTerminalSnapshot(null);
          terminalTimerRef.current = null;
        }, TERMINAL_GRACE_MS);
      } else if (nextActive) {
        // A new (or same) active loop is running — clear any
        // lingering terminal snapshot so the pill can update.
        if (terminalSnapshot) {
          if (terminalTimerRef.current) {
            clearTimeout(terminalTimerRef.current);
            terminalTimerRef.current = null;
          }
          setTerminalSnapshot(null);
        }
      }
      lastActiveRef.current = nextActive;
      setActive(nextActive);
      setErr(null);
      // ── Iter 309 · Item E — chip-wins reconciliation ──────────
      // LoopStatusChip's polled /loop/active is the source of truth
      // for whether a loop is running and which phase it's in. On
      // an SSE reconnect gap, ChatPanel's SSE-derived `loopPhase`
      // can lag (or contradict) the actual backend state. Notify
      // the parent so it can reconcile setLoopPhase to match server
      // truth. Documented as a subtle invariant so future editors
      // don't remove it thinking it's redundant with SSE.
      if (typeof onPhaseUpdate === "function" && nextActive) {
        const p = nextActive.phase || nextActive.state || "";
        const s = nextActive.state || "";
        if (p) onPhaseUpdate(String(p).toLowerCase(), String(s).toLowerCase());
      }
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "loop status fetch failed");
    }
  }, [projectId, onPhaseUpdate, terminalSnapshot]);

  // Poll on mount + interval + on tab-focus (users often alt-tab
  // during a 20-min loop; refreshing the chip immediately when they
  // come back removes any perceived staleness).
  useEffect(() => {
    poll();
    const t = setInterval(poll, POLL_MS);
    const onFocus = () => poll();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(t);
      window.removeEventListener("focus", onFocus);
    };
  }, [poll]);

  // Reset stop-confirm state whenever the active loop changes id or
  // the loop terminates, so the button never gets stuck showing
  // "Confirm stop" against a stale target.
  useEffect(() => {
    setStopState("idle");
    if (confirmTimerRef.current) {
      clearTimeout(confirmTimerRef.current);
      confirmTimerRef.current = null;
    }
  }, [active?.loop_id]);

  // ── Iter 329 · Task 2 — "Done" click clears terminal grace ──
  // Founder wants an inline dismissal affordance next to the SHIPPED
  // label instead of relying on the 30s auto-timeout. Clicking Done
  // clears the snapshot + timer so the chip unmounts immediately.
  const onDoneClick = useCallback(() => {
    if (terminalTimerRef.current) {
      clearTimeout(terminalTimerRef.current);
      terminalTimerRef.current = null;
    }
    setTerminalSnapshot(null);
    // lastActiveRef stays set — a fresh loop kickoff will re-trigger
    // the running-then-null transition; no need to reset here.
  }, []);

  // ── Iter 323 · Bug B — clean up terminal grace timer on unmount ──
  useEffect(() => () => {
    if (terminalTimerRef.current) {
      clearTimeout(terminalTimerRef.current);
      terminalTimerRef.current = null;
    }
  }, []);

  const onStopClick = useCallback(async () => {
    if (!active?.loop_id) return;
    if (busy) return;
    if (stopState === "idle") {
      setStopState("confirming");
      confirmTimerRef.current = setTimeout(() => {
        setStopState("idle");
        confirmTimerRef.current = null;
      }, CONFIRM_WINDOW_MS);
      return;
    }
    if (stopState === "confirming") {
      if (confirmTimerRef.current) {
        clearTimeout(confirmTimerRef.current);
        confirmTimerRef.current = null;
      }
      setBusy(true);
      setStopState("stopping");
      try {
        await cancelLoop(active.loop_id);
        setStopState("stopped");
        // Give the backend a beat to write the terminal state + lock
        // release, then re-poll so the chip disappears cleanly.
        setTimeout(() => { poll(); setBusy(false); }, 900);
      } catch (e) {
        setErr(e?.response?.data?.detail || e?.message || "cancel failed");
        setStopState("idle");
        setBusy(false);
      }
    }
  }, [active, busy, stopState, poll]);

  // ── Iter 323 · Bug B — relaxed unmount guard ────────────────
  // Chip must stay mounted through the terminal grace window so
  // the founder sees the SHIPPED confirmation pill. Prior guard
  // (`if (!active && !err) return null`) vanished the chip the
  // instant the loop went COMPLETED because /loop/active filters
  // terminal loops.
  if (!active && !err && !terminalSnapshot) return null;

  // The chip's display source is either the live active loop or —
  // during the grace window — the terminal snapshot we captured.
  const displayLoop = active || terminalSnapshot;
  const isTerminal = !active && !!terminalSnapshot;

  const stopLabel =
    stopState === "confirming" ? "Confirm stop"
    : stopState === "stopping" ? "Stopping…"
    : stopState === "stopped"  ? "Stopped"
    : "Stop loop";

  // ── Iter 325 · Terminal failure styling ─────────────────────────
  // A failed / aborted / expired terminal must render RED, not the
  // green "SHIPPED" styling that suits a completed run. Founder
  // screenshot: "Failed" plan bubble but chip visually missing or
  // painted green — reads as "still running". Now the chip
  // explicitly detects failure states and switches the border /
  // dot / label colours to red so the founder can't miss it.
  const terminalStateLc = String(displayLoop?.state || "").toLowerCase();
  const isTerminalFailure = isTerminal && (
    terminalStateLc === "failed"
    || terminalStateLc === "aborted"
    || terminalStateLc === "expired"
  );
  const accentColor = err || isTerminalFailure
    ? C.red
    : (active ? C.green + "55" : (isTerminal ? C.green + "88" : C.border));
  const dotColor = err || isTerminalFailure
    ? C.red
    : (displayLoop ? C.green : C.dim);
  const phaseColor = err || isTerminalFailure ? C.red : C.green;

  return (
    <div
      data-testid="loop-status-chip"
      data-terminal={isTerminal ? "true" : "false"}
      data-terminal-outcome={
        isTerminalFailure ? "failure"
          : (isTerminal ? "success" : "running")
      }
      style={{
        position: "sticky", top: 0, zIndex: 20,
        display: "flex", alignItems: "center", gap: 10,
        padding: "6px 10px",
        margin: "0 0 6px 0",
        background: C.bg,
        border: `1px solid ${accentColor}`,
        borderRadius: 8,
        fontFamily: C.mono,
        fontSize: 12,
        color: C.text,
        boxShadow: "0 2px 8px rgba(0,0,0,0.35)",
      }}
    >
      {/* Pulsing status dot — solid green during grace, no pulse */}
      <span
        aria-hidden
        style={{
          width: 8, height: 8, borderRadius: "50%",
          background: dotColor,
          boxShadow: (active && !err) || (isTerminal && !isTerminalFailure)
            ? `0 0 8px ${C.green}`
            : (isTerminalFailure ? `0 0 8px ${C.red}` : "none"),
          animation: active && !err ? "loopChipPulse 1.4s ease-in-out infinite" : "none",
          flex: "0 0 auto",
        }}
      />

      {err ? (
        <span data-testid="loop-status-chip-error" style={{ color: C.red }}>
          Loop status error · <span style={{ color: C.dim }}>{err}</span>
        </span>
      ) : (
        <>
          <span data-testid="loop-status-chip-phase" style={{ color: phaseColor, letterSpacing: "0.06em" }}>
            LOOP · {phaseText(displayLoop)}
          </span>
          <span
            data-testid="loop-status-chip-id"
            title={displayLoop?.loop_id || ""}
            style={{ color: C.dim, marginLeft: 2 }}
          >
            id · <span style={{ color: C.text }}>{(displayLoop?.loop_id || "").slice(-8)}</span>
          </span>

          <span style={{ flex: 1 }} />

          {/*
            Stop button — only rendered when the loop is genuinely
            active. During the terminal grace window (Iter 323 Bug B)
            the loop is already done, so hiding the Stop CTA avoids
            a confusing "Stop a shipped loop" affordance.
          */}
          {active && !isTerminal && (
            <button
              type="button"
              data-testid="loop-status-chip-stop"
              aria-label={stopState === "confirming" ? "Confirm stop loop" : "Stop loop"}
              aria-pressed={stopState === "confirming"}
              onClick={onStopClick}
              disabled={busy && stopState !== "confirming"}
              style={{
                appearance: "none",
                background: stopState === "confirming" ? C.red : "transparent",
                color: stopState === "confirming" ? "#000" : C.red,
                border: `1px solid ${C.red}`,
                borderRadius: 6,
                padding: "3px 10px",
                fontFamily: C.mono,
                fontSize: 11,
                letterSpacing: "0.06em",
                cursor: busy ? "wait" : "pointer",
                textTransform: "uppercase",
                transition: "background 120ms ease, color 120ms ease",
              }}
            >
              {stopLabel}
            </button>
          )}
          {/*
            Iter 329 · Task 2 — inline "Done" affordance during the
            terminal-success grace window. Replaces the dark-overlay
            ship modal's "Close" button. Clicking dismisses the chip
            immediately by clearing the terminal snapshot + grace
            timer (chip unmounts via the `!active && !terminalSnapshot`
            gate at line 278). Not rendered on terminal-failure —
            failures still show the (already amber/red) label until
            the grace expires so the user can see what went wrong.
          */}
          {isTerminal && !isTerminalFailure && (
            <button
              type="button"
              data-testid="loop-status-chip-done"
              aria-label="Dismiss shipped loop status"
              onClick={onDoneClick}
              style={{
                appearance: "none",
                background: "transparent",
                color: C.green,
                border: `1px solid ${C.green}88`,
                borderRadius: 6,
                padding: "3px 10px",
                fontFamily: C.mono,
                fontSize: 11,
                letterSpacing: "0.06em",
                cursor: "pointer",
                textTransform: "uppercase",
                transition: "background 120ms ease",
              }}
            >
              Done
            </button>
          )}
        </>
      )}

      <style>{`
        @keyframes loopChipPulse {
          0%   { opacity: 1;   transform: scale(1); }
          50%  { opacity: 0.55; transform: scale(1.25); }
          100% { opacity: 1;   transform: scale(1); }
        }
      `}</style>
    </div>
  );
}
