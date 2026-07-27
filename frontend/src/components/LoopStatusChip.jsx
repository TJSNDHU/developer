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
import { getActiveLoop, cancelLoop } from "../lib/loopApi";

const POLL_MS = 10_000;
const CONFIRM_WINDOW_MS = 4_000;

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
};

function phaseText(active) {
  if (!active) return "IDLE";
  // Prefer the finer-grained `phase` field; fall back to `state`.
  const key = String(active.phase || active.state || "").toLowerCase();
  return PHASE_LABEL[key] || key.toUpperCase() || "RUNNING";
}

export default function LoopStatusChip({ projectId = null, onPhaseUpdate = null }) {
  const [active, setActive] = useState(null);
  const [err, setErr]       = useState(null);
  const [busy, setBusy]     = useState(false);
  // Confirm-again state: "idle" | "confirming" | "stopping" | "stopped"
  const [stopState, setStopState] = useState("idle");
  const confirmTimerRef = useRef(null);

  const poll = useCallback(async () => {
    try {
      const data = await getActiveLoop(projectId);
      const nextActive = data?.active || null;
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
        // Iter 312 · Class 3 companion — pass BOTH state and phase so
        // the parent can apply the same plan-variant remap it uses in
        // handleLoopEvent (awaiting_confirmation + phase=plan →
        // plan_pending so PlanApprovalCard's showPlanCard gate
        // remains true). Previously we sent only `phase` which caused
        // the parent to overwrite loopPhase='plan_pending' with the
        // raw 'plan', suppressing the recovered approval card. Kept
        // second-arg optional so any other caller passing just `p`
        // still gets the legacy behavior via string signature.
        const p = nextActive.phase || nextActive.state || "";
        const s = nextActive.state || "";
        if (p) onPhaseUpdate(String(p).toLowerCase(), String(s).toLowerCase());
      }
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "loop status fetch failed");
    }
  }, [projectId, onPhaseUpdate]);

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

  if (!active && !err) return null;

  const stopLabel =
    stopState === "confirming" ? "Confirm stop"
    : stopState === "stopping" ? "Stopping…"
    : stopState === "stopped"  ? "Stopped"
    : "Stop loop";

  return (
    <div
      data-testid="loop-status-chip"
      style={{
        position: "sticky", top: 0, zIndex: 20,
        display: "flex", alignItems: "center", gap: 10,
        padding: "6px 10px",
        margin: "0 0 6px 0",
        background: C.bg,
        border: `1px solid ${err ? C.red : (active ? C.green + "55" : C.border)}`,
        borderRadius: 8,
        fontFamily: C.mono,
        fontSize: 12,
        color: C.text,
        boxShadow: "0 2px 8px rgba(0,0,0,0.35)",
      }}
    >
      {/* Pulsing status dot */}
      <span
        aria-hidden
        style={{
          width: 8, height: 8, borderRadius: "50%",
          background: err ? C.red : (active ? C.green : C.dim),
          boxShadow: active && !err ? `0 0 8px ${C.green}` : "none",
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
          <span data-testid="loop-status-chip-phase" style={{ color: C.green, letterSpacing: "0.06em" }}>
            LOOP · {phaseText(active)}
          </span>
          <span
            data-testid="loop-status-chip-id"
            title={active.loop_id}
            style={{ color: C.dim, marginLeft: 2 }}
          >
            id · <span style={{ color: C.text }}>{(active.loop_id || "").slice(-8)}</span>
          </span>

          <span style={{ flex: 1 }} />

          {/*
            Stop button — deliberately styled as an OBVIOUS action
            control (outlined red, action-tone label). Distinct in
            colour + shape from any diagnostic/info badge nearby.
            Uses a 4-s click-again-to-confirm pattern so a single
            stray click can never abort a 20-min loop.
          */}
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
