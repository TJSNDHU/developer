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
  Check, AlertTriangle, AlertOctagon, Loader2,
} from "lucide-react";

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
export default function LoopLiveFeed({ loopId, event, terminal, phase }) {
  // Full history of raw SSE events we've seen. `foldNarrations` will
  // dedupe by correlation_id, so we can safely keep the full stream —
  // the bound is enforced downstream.
  const [events, setEvents] = useState([]);
  const [nowEpoch, setNowEpoch] = useState(() => Date.now() / 1000);
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

  const folded = useMemo(() => foldNarrations(events), [events]);
  const hasLines = folded.length > 0;

  // Auto-scroll to latest whenever a new narration lands.
  const scrollToBottom = useCallback(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);
  useEffect(() => { scrollToBottom(); }, [folded.length, scrollToBottom]);

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
            {folded.length} event{folded.length === 1 ? "" : "s"}
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
        {hasLines ? folded.map((line) => (
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
      </div>

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
