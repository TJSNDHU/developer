// ── Iter 329 · Task 2 — Ship info extractor ────────────────────────
// Walks the raw event stream newest → oldest for a terminal
// state=completed · phase=ship frame carrying `data.commit_sha` (that
// is the exact shape the loop_engine emits for a successful ship,
// verified by the modal-dispatch site that used to consume it at
// ChatPanel.jsx line ~2673 pre-Task-2). Returns null when the loop
// isn't done or wasn't a ship. Kept pure/exported so tests can lock
// in the extraction contract without mounting the full component.
export function extractShipInfo(events) {
  if (!Array.isArray(events)) return null;
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (!ev) continue;
    const state = String(ev.state || "").toLowerCase();
    const phase = String(ev.phase || "").toLowerCase();
    const d = ev.data || {};
    if ((state === "completed" || state === "done")
        && phase === "ship"
        && d.commit_sha) {
      const fullSha = d.full_sha || d.commit_sha;
      const shortSha = String(d.commit_sha).slice(0, 7);
      return {
        commitSha:   String(d.commit_sha),
        fullSha:     String(fullSha),
        shortSha,
        htmlUrl:     d.html_url || null,
        files:       d.files_changed || [],
        commitMsg:   d.commit_message || null,
      };
    }
  }
  return null;
}

// ── Iter 329 · Task 2 — inline Shipped row ─────────────────────────
// Renders "Shipped {sha7} · View on GitHub · Rollback" as a
// persistent line at the bottom of the feed on terminal-success.
// Replaces the dark-overlay ShipConfirmModal that previously fired
// via `aurem:open-ship-modal` on every loop-mode ship.
//
// Rollback wiring: calls the PROVEN POST /loop/{id}/rollback
// (Iter 329 Deploy 3-A · production-verified with commit 5d939a4 →
// revert ea3ebcf on 2026-07-27). Two-click safety: first click sets
// state="confirming" with a 10s auto-timeout back to idle; second
// click within the window kicks the real revert. Progress updates
// via a lightweight poll of /loop/{id}/status until
// rollback_status ∈ {done, failed}. Zero force-push semantics
// because the backend uses github_api_writer.revert_commit which
// produces a new revert commit (history preserved).
//
// Iter 329 · Fix C — confirm-click hardening (real production bug):
// Founder reported the second click never firing POST even though
// data-rollback-phase advanced to "confirming" on first click.
// Root causes:
//   (1) Bug X — parent unmounts (fixed via Fix A dropping the
//       `terminal` gate). Row now persists across parent re-renders.
//   (2) Bug Y — `useCallback` with `phase` in deps captured a stale
//       phase from an old render under React 18 concurrent
//       scheduling. Now the callback reads `phaseRef.current` (a
//       useRef mirror synced via effect) so it ALWAYS sees the
//       latest phase, regardless of when the callback was created.
//   (3) Insufficient visual feedback — bumped confirm window from
//       4s → 10s + high-contrast red-fill with white text +
//       distinct data-testid suffix so DOM inspection can prove
//       the confirming state visually landed.
const ROLLBACK_CONFIRM_MS = 10_000;

export function ShippedRow({ loopId, ship, onDone, onRollbackStarted }) {
  const [phase, setPhase] = useState("idle");
  // idle | confirming | submitting | handed-off | failed
  //
  // Iter 330 · Path P1 · state-machine rewrite:
  //   • Removed poll-based rollbackSha/rollbackHtmlUrl tracking —
  //     OperationHistory now owns rollback progress via SSE. On the
  //     confirm click we fire POST /rollback then call
  //     onRollbackStarted(loopId) so the parent lifts activeLoopId
  //     into OperationHistory, which opens the /stream subscription.
  //   • "queued"/"running"/"done" phases (poll-derived) collapsed
  //     into a single terminal "handed-off" phase — the UI intent
  //     is just "button disabled; look at OperationHistory above".
  //   • Iter 329 · Fix C's phaseRef + 10s confirm window + diagnostic
  //     events retained verbatim (proven correct via preview harness).
  const [error, setError] = useState(null);
  const confirmTimerRef = useRef(null);

  // Iter 329 · Fix C — phaseRef mirrors phase state so the click
  // callback always reads the latest value, immune to stale-closure
  // regardless of when React commits the setPhase update.
  const phaseRef = useRef("idle");
  useEffect(() => { phaseRef.current = phase; }, [phase]);

  // Iter 330 · Test B guard — in-flight POST ref. Prevents rapid
  // double-clicks in the "confirming" state from firing multiple
  // POST /rollback requests OR triggering onRollbackStarted twice.
  // Ref-based (not state) so the guard is sync-readable in the same
  // tick as the click, before React commits any state change.
  const inFlightRef = useRef(false);

  const clearConfirmTimer = useCallback(() => {
    if (confirmTimerRef.current) {
      clearTimeout(confirmTimerRef.current);
      confirmTimerRef.current = null;
    }
  }, []);
  useEffect(() => () => { clearConfirmTimer(); }, [clearConfirmTimer]);

  // Iter 329 · Fix C · diagnostic pass — production repro instrumentation
  // (no functional change). Emits two window events the founder can
  // listen on from DevTools to inspect the actual click + render
  // behaviour on the live production URL.
  //   • aurem:debug:rollback-click — fires on EVERY click, with the
  //     phaseRef.current value AND the reactive phase state at read
  //     time. Distinguishes fiber-inspection-caches vs true state.
  //   • aurem:debug:shipped-row-render — fires after EVERY ShippedRow
  //     render, revealing remount vs re-render behaviour + phase
  //     value at render time.
  useEffect(() => {
    try {
      window.dispatchEvent(new CustomEvent(
        "aurem:debug:shipped-row-render",
        { detail: {
          phase,
          shortSha: ship.shortSha,
          timestamp: Date.now(),
        } },
      ));
    } catch { /* noop */ }
  });

  // Iter 329 · Fix C + Iter 330 · Path P1 — callback reads phaseRef +
  // inFlightRef, NOT closure `phase`. Deps intentionally omit `phase`
  // so we don't recreate on every phase transition (which would
  // reintroduce the stale-closure class of bug). The inFlightRef
  // guard blocks rapid double-clicks from firing the POST twice
  // (Test B).
  const onRollbackClick = useCallback(async () => {
    const current = phaseRef.current;
    try {
      window.dispatchEvent(new CustomEvent(
        "aurem:debug:rollback-click",
        { detail: {
          phaseRefRead: current,
          stateAtRead:  phase,      // captured from render closure
          tick: Date.now(),
          loopId,
          shortSha: ship.shortSha,
          inFlight: inFlightRef.current,
        } },
      ));
    } catch { /* noop */ }

    // Iter 339 — console-visible trace (the CustomEvents above have no
    // listener on prod; this line is the founder's behavioural proof
    // that the handler ran and what phase it read).
    // eslint-disable-next-line no-console
    console.debug("[rollback] click", {
      phaseRead: current, inFlight: inFlightRef.current,
      loopId, sha: ship.shortSha,
    });

    // Iter 330 · Test B — in-flight guard. Silently drop if a POST
    // is already in progress. The guard is set BEFORE any await so
    // React fiber scheduling can never squeeze a second click through.
    if (inFlightRef.current) return;

    if (current === "idle") {
      setPhase("confirming");
      confirmTimerRef.current = setTimeout(() => {
        // Only revert if we're still in confirming (guard against
        // race where user clicked twice fast and phase already moved
        // past confirming).
        if (phaseRef.current === "confirming") setPhase("idle");
        confirmTimerRef.current = null;
      }, ROLLBACK_CONFIRM_MS);
      return;
    }
    if (current === "confirming") {
      inFlightRef.current = true;
      clearConfirmTimer();
      setPhase("submitting");
      setError(null);
      try {
        // eslint-disable-next-line no-console
        console.debug("[rollback] POST /loop/%s/rollback firing", loopId);
        await rollbackLoop(loopId);
        // eslint-disable-next-line no-console
        console.debug("[rollback] POST ok — handed off to OperationHistory");
        // Success → hand off to OperationHistory. The parent lifts
        // this loopId into activeLoopId which triggers OperationHistory
        // to open the /stream subscription and render live progress.
        // Iter 330 · Test B — inFlightRef stays true after successful
        // POST so any spurious late click cannot re-trigger the flow.
        setPhase("handed-off");
        if (typeof onRollbackStarted === "function") {
          onRollbackStarted(loopId);
        }
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn("[rollback] POST failed:", e?.response?.data?.detail || e?.message);
        // POST failed → revert to idle so user can retry.
        inFlightRef.current = false;
        setPhase("failed");
        setError(
          e?.response?.data?.detail || e?.message || "Rollback failed",
        );
      }
    }
  }, [loopId, clearConfirmTimer, ship.shortSha, onRollbackStarted]);
  // Note: `phase` intentionally omitted from deps — the whole point of
  // phaseRef is to read the freshest value without recreating the
  // callback on every phase change. Keeping `stateAtRead` in the
  // diagnostic dispatch captures the closure-scoped `phase` (may lag
  // behind phaseRef.current) which is exactly the data needed to
  // diagnose stale-closure vs live-render mismatch in production.

  const rollbackLabel =
    phase === "confirming" ? "Confirm rollback"
    : phase === "submitting"? "Rolling back…"
    : phase === "handed-off"? "Rolling back — see history"
    : phase === "failed"    ? "Retry rollback"
    : "Rollback";

  return (
    <div
      data-testid={`loop-shipped-row-${ship.shortSha}`}
      data-rollback-phase={phase}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "6px 0 3px 0",
        borderTop: "1px dashed #ffffff1a",
        marginTop: 6,
      }}
    >
      <Check
        size={12}
        strokeWidth={2.5}
        style={{ color: "#22C55E", flexShrink: 0 }}
      />
      <span
        data-testid={`loop-shipped-label-${ship.shortSha}`}
        style={{ color: "#e6ebf3" }}
      >
        Shipped{" "}
        <span style={{
          color: "#22C55E",
          fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        }}>
          {ship.shortSha}
        </span>
      </span>
      {ship.htmlUrl && (
        <a
          data-testid={`loop-shipped-github-${ship.shortSha}`}
          href={ship.htmlUrl}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            color: "#9aa0a8", textDecoration: "none",
            display: "inline-flex", alignItems: "center", gap: 4,
            fontSize: 11,
          }}
        >
          View on GitHub <ExternalLink size={10} strokeWidth={2.5} />
        </a>
      )}
      <span style={{ flex: 1 }} />
      <button
        type="button"
        data-testid={
          phase === "confirming"
            ? `loop-shipped-rollback-btn-confirming-${ship.shortSha}`
            : `loop-shipped-rollback-btn-${ship.shortSha}`
        }
        aria-label={phase === "confirming" ? "Confirm rollback" : "Rollback loop"}
        aria-pressed={phase === "confirming"}
        disabled={phase === "submitting" || phase === "handed-off"}
        onClick={onRollbackClick}
        style={{
          appearance: "none",
          background: phase === "confirming" ? "#EF4444" : "transparent",
          color:      phase === "confirming" ? "#ffffff" : "#f87171",
          border:     phase === "confirming"
            ? "1px solid #FCA5A5"
            : "1px solid #EF4444",
          borderRadius: 6,
          padding: "3px 10px",
          fontFamily: "'JetBrains Mono', ui-monospace, monospace",
          fontSize: 10.5,
          fontWeight: phase === "confirming" ? 700 : 500,
          letterSpacing: "0.04em",
          cursor: (phase === "submitting" || phase === "handed-off")
            ? "wait" : "pointer",
          opacity: (phase === "submitting" || phase === "handed-off")
            ? 0.6 : 1,
          display: "inline-flex", alignItems: "center", gap: 4,
          textTransform: "uppercase",
          transition: "background 120ms ease, color 120ms ease, border-color 120ms ease",
          boxShadow: phase === "confirming"
            ? "0 0 0 2px rgba(239, 68, 68, 0.35)"
            : "none",
        }}
      >
        <RotateCcw size={9} strokeWidth={2.5} />
        {rollbackLabel}
      </button>
      {phase === "failed" && error && (
        <span
          data-testid="loop-shipped-rollback-error"
          style={{ color: "#f87171", fontSize: 10 }}
          title={error}
        >
          {String(error).slice(0, 40)}
        </span>
      )}
    </div>
  );
}

/**
 * LoopLiveFeed — Iter 309 · Live Narration rewrite
 *
 * REPLACES the old iter 275/278/288/308 LoopLiveFeed which mixed:
 *   • real event tag/message rows
 *   • dimmed heartbeat "waiting" rows (Item A — REMOVED)
 *   • 10-second gap-fallback heuristic ("usually 25-40s…", Item B — REMOVED)
 *   • 24-line per-phase empty-state placeholder switch (Item C — SIMPLIFIED)
 *
 * NEW BEHAVIOUR:
 *   • Renders one line per `data.type === "narration"` event.
 *   • Icon + text, tone-coloured, fade-in on arrival, auto-scroll to
 *     latest.
 *   • Any narration with a `correlation_id` that has NOT yet been
 *     paired by a subsequent success/warning/danger narration on the
 *     SAME correlation_id shows a live-ticking elapsed timer next to
 *     it. Timer baseline is the event's server-side `ts_epoch`
 *     (numeric), NOT client `Date.now()` at receipt — so reconnect +
 *     gap replay show TRUE server-time elapsed, not reconnect-relative
 *     wall time.
 *   • Once the paired resolving event arrives, the timer is removed
 *     and the line is locked to its final icon/tone.
 *   • Non-narration events (state-transition frames like
 *     awaiting_confirmation / completed / failed) are intentionally
 *     NOT rendered here — those already surface via LoopStepBar,
 *     LoopStatusChip, PlanApprovalCard, ShipPendingCard, and the
 *     terminal fail/complete message bubble. Zero duplication.
 *
 * ZERO MOCKS INVARIANT (founder directive):
 *   • Timer intervals are the ONLY setInterval in this file (100ms
 *     tick to advance `now`). That interval reads real `ts_epoch`
 *     values from real server events — it does NOT simulate progress
 *     independently.
 *   • Empty-state placeholder disappears the moment the first real
 *     narration event lands.
 */
import React, {
  useCallback, useEffect, useMemo, useRef, useState,
} from "react";
import {
  Check, AlertTriangle, AlertOctagon, Loader2, ExternalLink, RotateCcw,
} from "lucide-react";
import { rollbackLoop } from "../lib/loopApi";
import OperationHistory from "./OperationHistory";

// ── Tone → icon + colour ────────────────────────────────────────────
const TONE_STYLES = {
  pending: {
    color: "#FF6608",
    Icon:  Loader2,
    spin:  true,
  },
  success: {
    color: "#22C55E",
    Icon:  Check,
    spin:  false,
  },
  warning: {
    color: "#F5A524",
    Icon:  AlertTriangle,
    spin:  false,
  },
  danger: {
    color: "#EF4444",
    Icon:  AlertOctagon,
    spin:  false,
  },
};

const MAX_LINES = 60;      // Bounded ring buffer for long loops.
const TICK_MS   = 100;     // Timer refresh cadence (client-side).

// Extract narration payload if this SSE event is a narration frame.
// Returns null for non-narration events.
export function extractNarration(ev) {
  const d = ev && ev.data;
  if (!d || d.type !== "narration") return null;
  return {
    tone:            String(d.tone || "pending"),
    step:            String(d.narration_step || ""),
    text:            String(d.narration_text || ev.message || ""),
    correlationId:   String(d.correlation_id || ""),
    tsEpoch:         typeof d.ts_epoch === "number" ? d.ts_epoch : null,
    // Fallback ordering key when ts_epoch is missing (defensive —
    // should never happen with the current backend contract).
    fallbackOrderTs: ev._rxAt || Date.now(),
  };
}

// ── Iter 329 · Fix B — terminal-tone resolver ───────────────────────
// Bug (confirmed live on ship commit 1f70444): when a loop reaches a
// terminal state — COMPLETED for ship-success OR aborted/cancelled/
// expired/failed — any narration line that was still `tone=pending`
// at that moment kept spinning + the elapsed timer kept ticking (or
// worse: crossed the 60s stall threshold and lit up "(stalled)" in
// red) because the backend never emitted the correlation_id-matching
// resolver frame for that intermediate line. The founder shipped
// successfully at the top-level status but the feed still visually
// showed "Writing… (stalled)" / "Running scan… (stalled)".
//
// Root cause class: SSE gap OR backend narration omission. This is
// NOT reliably fixable by "just emit the missing frame" from the
// backend — every phase in loop_engine would need audit + the SSE
// gap window can drop late frames anyway. The correct place to fix
// is here at the RENDER layer: once the loop is terminal, we know
// with certainty that no line is legitimately still in-progress, so
// every pending line MUST be flipped to a resolved tone.
//
// Resolution rule:
//   • Loop ended in `completed`  → pending → success (green tick)
//   • Loop ended in `failed`/`error` → pending → danger (red)
//   • Everything else terminal (`aborted`/`cancelled`/`expired`/
//     `paused_for_user` that never resumed) → pending → warning
//     (amber). We do NOT know if the phase was actually done — just
//     that it will never resolve for the user's current session.
//
// Fallback: if terminal=true but no event carries a recognisable
// `state`, we conservatively resolve to `success` — the least-worst
// UI choice; the alternative is leaving a stalled spinner up forever.

const TERMINAL_TONE_BY_STATE = {
  completed: "success",
  done:      "success",
  failed:    "danger",
  error:     "danger",
  aborted:   "warning",
  cancelled: "warning",
  canceled:  "warning",
  expired:   "warning",
  paused_for_user: "warning",
};

export function resolveTerminalTone(events) {
  // Walk newest → oldest, find the first event that carries a
  // meaningful `state` field. This is the loop's terminal verdict.
  for (let i = events.length - 1; i >= 0; i--) {
    const s = events[i] && events[i].state;
    if (!s) continue;
    return TERMINAL_TONE_BY_STATE[String(s).toLowerCase()] || "success";
  }
  return "success";
}

// Iter 331 — mid-run frontier resolver (same class as
// resolvePendingOnTerminal, but for a LIVE loop). The engine is
// strictly sequential: a narration from step N proves every step < N
// finished. Any line still "pending" for an earlier step is a stale
// spinner (missed SSE resolver frame or backend omission — founder
// screenshot: "Writing tests/test_smoke.py ↻ 44s" while the loop sat
// at the SHIP gate). Pure — safe in a memo.
const NARRATION_STEP_ORDER = { plan: 1, execute: 2, verify: 3, scan: 4, ship: 5 };

export function resolveStalePendingByFrontier(folded) {
  let frontier = 0;
  for (const line of folded) {
    const o = NARRATION_STEP_ORDER[line.step] || 0;
    if (o > frontier) frontier = o;
  }
  if (!frontier) return folded;
  return folded.map((line) => {
    const o = NARRATION_STEP_ORDER[line.step] || 0;
    return (line.tone === "pending" && o && o < frontier)
      ? { ...line, tone: "success", __resolvedByFrontier: true }
      : line;
  });
}

// Transform a folded narration list. If `terminal` is true, every
// still-pending line is rewritten to `terminalTone`. Pure — safe to
// call in a memo. The `__resolvedOnTerminal` marker is exported for
// test assertions + optional visual differentiation.
export function resolvePendingOnTerminal(folded, terminal, terminalTone) {
  if (!terminal) return folded;
  const tone = terminalTone || "success";
  return folded.map((line) => (
    line.tone === "pending"
      ? { ...line, tone, __resolvedOnTerminal: true }
      : line
  ));
}

// Fold a list of narration events into an ordered, deduplicated list.
// Later events on the same correlation_id RESOLVE the earlier pending
// one (i.e., we keep exactly one entry per correlation_id — the latest
// tone/text wins, but the ORIGINAL ts_epoch is preserved so the timer
// stays anchored to the true "started at" moment for display purposes
// while the timer itself is REMOVED once tone != "pending").
export function foldNarrations(events) {
  const byCorr = new Map();       // correlationId → entry (or synthetic id)
  const ordered = [];             // display order (arrival order of the
                                  // FIRST event per correlationId)
  for (const ev of events) {
    const n = extractNarration(ev);
    if (!n) continue;
    const key = n.correlationId || `__anon_${n.text}_${n.tsEpoch}`;
    const existing = byCorr.get(key);
    if (existing) {
      // Resolving event: update tone/text, keep original ts_epoch so
      // the finished line shows how long the pending phase actually
      // took (server-elapsed at moment of resolution).
      existing.tone = n.tone;
      existing.text = n.text;
      existing.resolvedTsEpoch = n.tsEpoch;
    } else {
      const entry = { key, ...n };
      byCorr.set(key, entry);
      ordered.push(entry);
    }
  }
  // Bound length — drop oldest.
  if (ordered.length > MAX_LINES) return ordered.slice(-MAX_LINES);
  return ordered;
}

function formatElapsed(sec) {
  if (sec < 10) return sec.toFixed(1) + "s";
  if (sec < 60) return Math.floor(sec) + "s";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}m ${s}s`;
}

// ── Per-line row ────────────────────────────────────────────────────
function NarrationLine({ line, nowEpoch }) {
  const style = TONE_STYLES[line.tone] || TONE_STYLES.pending;
  const Icon  = style.Icon;

  // Timer is shown ONLY while tone === "pending" AND we have an
  // anchor ts_epoch AND we have current time. This is the ONE piece
  // of client-side wall-clock tick — but its BASELINE is the server
  // ts_epoch, NOT the receipt time. So on SSE reconnect + gap replay,
  // the timer correctly reflects server-side elapsed, not
  // reconnect-relative time (founder Part 1.4 requirement).
  const showTimer = line.tone === "pending" && line.tsEpoch && nowEpoch;
  const elapsedSec = showTimer
    ? Math.max(0, nowEpoch - line.tsEpoch)
    : 0;

  // ── Iter 324 · Fix C — stalled-narration indicator ──────────
  // Founder screenshot: two "Writing X" narrations stayed at
  // "23s · 23s" because the SUCCESS resolve frames never landed
  // (SSE gap OR backend never emitted the correlation_id-matching
  // "wrote X" narration). Now: when a pending narration exceeds
  // STALL_THRESHOLD_S (60 s), swap the icon colour to grey/red
  // hint + append "(stalled)" so the founder sees the pipeline
  // is not "still working" — it's stuck.
  const STALL_THRESHOLD_S = 60;
  const isStalled = showTimer && elapsedSec > STALL_THRESHOLD_S;
  const iconColor = isStalled ? "#ef4444" : style.color;
  const timerColor = isStalled ? "#ef4444" : "#9aa0a8";

  return (
    <div
      data-testid={`loop-narration-line-${line.key}`}
      data-tone={line.tone}
      data-stalled={isStalled ? "true" : "false"}
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "3px 0", lineHeight: 1.5,
        animation: "narration-fade-in 220ms ease-out",
      }}
    >
      <Icon
        size={12}
        strokeWidth={2.5}
        style={{
          color: iconColor,
          flexShrink: 0,
          animation: style.spin && !isStalled ? "narration-spin 1s linear infinite" : "none",
        }}
      />
      <span
        data-testid={`loop-narration-text-${line.key}`}
        style={{ flex: 1, color: line.tone === "pending" ? "#c9cbcf" : "#e6ebf3" }}
      >
        {line.text}
        {isStalled && (
          <span
            data-testid={`loop-narration-stalled-${line.key}`}
            style={{ marginLeft: 6, color: "#ef4444", fontSize: 10 }}
          >
            (stalled)
          </span>
        )}
      </span>
      {showTimer && (
        <span
          data-testid={`loop-narration-timer-${line.key}`}
          style={{
            fontFamily: "'JetBrains Mono', ui-monospace, monospace",
            fontSize: 10.5,
            color: timerColor,
            minWidth: 42,
            textAlign: "right",
          }}
        >
          {formatElapsed(elapsedSec)}
        </span>
      )}
    </div>
  );
}

// ── Component ───────────────────────────────────────────────────────
export default function LoopLiveFeed({ loopId, event, terminal, phase, projectId }) {
  // Full history of raw SSE events we've seen. `foldNarrations` will
  // dedupe by correlation_id, so we can safely keep the full stream —
  // the bound is enforced downstream.
  const [events, setEvents] = useState([]);
  const [nowEpoch, setNowEpoch] = useState(() => Date.now() / 1000);
  // Iter 330 · Path P1 — activeRollbackLoopId lifts the loopId into
  // OperationHistory's SSE subscription surface when a rollback POST
  // succeeds. ShippedRow.onRollbackStarted fires this exactly once
  // per rollback attempt (guarded by inFlightRef there).
  const [activeRollbackLoopId, setActiveRollbackLoopId] = useState(null);
  const scrollerRef = useRef(null);

  // Append each real event as it arrives.
  useEffect(() => {
    if (!event) return;
    setEvents((prev) => [...prev, { ...event, _rxAt: Date.now() }]);
  }, [event]);

  // Reset the buffer when the loop_id changes (e.g., new loop
  // kicked off in the same session).
  useEffect(() => {
    setEvents([]);
  }, [loopId]);

  // Timer tick — 100ms cadence keeps the elapsed reading smooth
  // without hammering render. Stops on terminal.
  useEffect(() => {
    if (terminal) return;
    const iv = setInterval(
      () => setNowEpoch(Date.now() / 1000),
      TICK_MS,
    );
    return () => clearInterval(iv);
  }, [terminal]);

  const folded = useMemo(
    () => resolveStalePendingByFrontier(foldNarrations(events)),
    [events],
  );
  // Iter 329 · Fix B — resolve any still-pending narration lines when
  // the loop reaches a terminal state. See resolvePendingOnTerminal
  // above for the rule table + rationale.
  const terminalTone = useMemo(() => resolveTerminalTone(events), [events]);
  const displayLines = useMemo(
    () => resolvePendingOnTerminal(folded, terminal, terminalTone),
    [folded, terminal, terminalTone],
  );
  // Iter 329 · Task 2 — extract ship info for the inline Shipped row.
  const shipInfo = useMemo(() => extractShipInfo(events), [events]);
  const hasLines = displayLines.length > 0;

  // Auto-scroll to latest whenever a new narration lands.
  const scrollToBottom = useCallback(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);
  useEffect(() => { scrollToBottom(); }, [displayLines.length, scrollToBottom]);

  if (!loopId) return null;

  // Item C — refined empty-state (single phase-aware line, not the
  // old 24-line switch). Phase interpolation keeps founder-visible
  // honesty ("which phase is starting") without branching complexity.
  const emptyLine = (() => {
    const p = (phase || "").toLowerCase();
    if (!p || p === "idle") return "~ Opening event stream…";
    if (p === "awaiting_confirmation")
      return "~ Plan ready — waiting for your approval…";
    if (p === "paused_for_user")
      return "~ Paused — waiting for your input…";
    if (p === "completed" || p === "done")
      return "~ Loop completed.";
    if (p === "failed" || p === "error" || p === "aborted" || p === "expired")
      return `~ Loop ended (${p}).`;
    // Standard running phases (planning / executing / verifying /
    // scanning / shipping / self_healing) → interpolate real phase.
    return `~ Opening ${p} stream…`;
  })();

  return (
    <div
      data-testid="loop-live-feed"
      data-state={hasLines ? "populated" : "pending"}
      style={{
        background: "#0F0F10",
        border:     "1px solid #ffffff14",
        borderRadius: 8,
        padding: "10px 12px",
        margin: "8px 0",
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        fontSize: 11.5,
        color: "#c9cbcf",
        maxHeight: 220,
        display: "flex", flexDirection: "column",
      }}
    >
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        fontSize: 10, letterSpacing: ".08em",
        color: "#9ca3af", marginBottom: 6,
        textTransform: "uppercase",
      }}>
        <span style={{
          width: 6, height: 6, borderRadius: "50%",
          background: terminal ? "#22c55e" : "#FF6608",
          boxShadow: terminal ? "none" : "0 0 8px #FF660888",
          animation: terminal ? "none" : "loop-pulse 1.4s ease-in-out infinite",
        }} />
        Loop {String(loopId).slice(0, 8)}  ·  live feed
        {hasLines && (
          <span style={{ marginLeft: "auto", color: "#94a3b8" }}>
            {displayLines.length} event{displayLines.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      <div
        ref={scrollerRef}
        data-testid="loop-live-feed-scroller"
        style={{
          flex: 1, overflowY: "auto", minHeight: 24, maxHeight: 175,
        }}
      >
        {hasLines ? displayLines.map((line) => (
          <NarrationLine
            key={line.key}
            line={line}
            nowEpoch={nowEpoch}
          />
        )) : (
          <div
            data-testid="loop-live-feed-placeholder"
            style={{ color: "#9aa0a8", fontStyle: "italic", fontSize: 11 }}
          >
            {emptyLine}
          </div>
        )}
        {/* Iter 329 · Task 2 · Fix A — persistent inline Shipped row
            on ship completion. Gate is `shipInfo` alone (not
            `shipInfo && terminal`). Rationale: extractShipInfo
            already requires state=completed + phase=ship +
            data.commit_sha; the server-side invariant (loop_engine.py
            2823-2944) guarantees commit_sha is populated ONLY after
            a real GitHub push, so shipInfo being truthy IS the
            terminal signal. The previous dual-gate coupled ShippedRow
            to the parent's `terminal` prop which could flicker false
            on unrelated re-renders (openLoopStream reset,
            heartbeats), unmounting the row and losing internal state
            (phase="confirming", timers). That was Bug X behind the
            confirm-click never firing on production. */}
        {shipInfo && (
          <ShippedRow
            loopId={loopId}
            ship={shipInfo}
            onRollbackStarted={setActiveRollbackLoopId}
          />
        )}
      </div>

      {/* Iter 330 · Path P1 — OperationHistory renders past + current
          ship/rollback ops as an auto-collapsing timeline. It opens
          its own /stream subscription ONLY when activeRollbackLoopId
          becomes non-null (i.e. after ShippedRow's confirm click has
          POSTed successfully). Passing projectId enables history
          hydration; without it the component still functions for the
          current-op live rollback flow. */}
      {projectId && (
        <OperationHistory
          projectId={projectId}
          activeLoopId={activeRollbackLoopId}
        />
      )}

      <style>{`
        @keyframes loop-pulse {
          0%,100% { opacity: 1;   transform: scale(1);   }
          50%     { opacity: 0.5; transform: scale(1.2); }
        }
        @keyframes narration-fade-in {
          from { opacity: 0; transform: translateY(2px); }
          to   { opacity: 1; transform: translateY(0);   }
        }
        @keyframes narration-spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
