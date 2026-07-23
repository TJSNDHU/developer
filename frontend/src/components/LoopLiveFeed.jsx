/**
 * LoopLiveFeed — Iter 275
 *
 * Compact live-feed panel that surfaces the LAST 4-5 real events
 * emitted by `GET /api/aurem-dev/loop/{loop_id}/stream`. Renders
 * inline above the composer while a loop is actively running.
 *
 * Honesty rules (per founder spec):
 *   • Every line shown corresponds to a real emitted event from
 *     `loop_engine.py`'s phase transitions (fields: phase, state,
 *     message). No canned rotating text.
 *   • During a genuine gap (no new event in ≥ 10 s), we show ONE
 *     subtle fallback line — clearly labeled as an estimate ("~"
 *     prefix) — using the LAST real phase we saw so the wording
 *     is contextual, not a lie.
 *   • The fallback line disappears the moment a real event arrives.
 *
 * Wiring: ChatPanel opens the SSE stream via `streamLoopEvents()`
 * and hands us each event via the `onEvent` prop. We just render
 * a bounded ring buffer — zero SSE plumbing here.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";

// Typical wall-clock windows we've measured from real prod runs;
// used ONLY as the "usually ~Ns" hint inside the gap fallback line.
// Not a promise, not a countdown, not fake progress — just honest
// context so the user knows silence is normal for this phase.
const PHASE_TYPICAL_S = {
  PLAN:      15,
  EXECUTE:   45,
  VERIFY:    30,
  SCAN:      15,
  SHIP:      10,
  SELF_HEAL: 20,
};

const PHASE_LABEL = {
  PLAN:      "Planning",
  EXECUTE:   "Execute",
  VERIFY:    "Verify",
  SCAN:      "Vanguard scan",
  SHIP:      "Ship",
  SELF_HEAL: "Self-heal",
};

const MAX_EVENTS = 5;
const GAP_MS     = 10_000;   // 10 s of silence → show fallback

function phaseColor(phase) {
  const p = (phase || "").toUpperCase();
  if (p === "PLAN")      return "#5B8DEF";
  if (p === "EXECUTE")   return "#FF6608";
  if (p === "VERIFY")    return "#a78bfa";
  if (p === "SCAN")      return "#facc15";
  if (p === "SHIP")      return "#22c55e";
  if (p === "SELF_HEAL") return "#f87171";
  return "#6b7280";
}

function formatEventLine(ev) {
  const ph = (ev?.phase || "").toUpperCase();
  const st = ev?.state || "";
  const msg = ev?.message || "";
  const sub = (ev?.data && ev.data.sub_step) || "";
  // Iter 278 — heartbeat frames are keepalives, not new steps.
  // Label them distinctly so the ring buffer visually distinguishes
  // "we're still waiting" from "something new happened".
  if (sub === "heartbeat" || ev?.data?.keepalive === true) {
    return { tag: "waiting", text: msg, keepalive: true };
  }
  if (st === "completed") return { tag: "SHIP",  text: `completed — ${msg}` };
  if (st === "failed")    return { tag: "FAIL",  text: msg || "failed" };
  if (st === "aborted")   return { tag: "ABRT",  text: msg || "aborted" };
  if (!ph) return { tag: "•", text: msg };
  return { tag: PHASE_LABEL[ph] || ph, text: msg };
}

export default function LoopLiveFeed({ loopId, event, terminal }) {
  const [events, setEvents] = useState([]);          // real events only
  const [now, setNow]       = useState(Date.now());
  const lastRealAt = useRef(Date.now());

  // Push each incoming real event onto the ring buffer.
  useEffect(() => {
    if (!event) return;
    lastRealAt.current = Date.now();
    setEvents((prev) => {
      const next = [...prev, { ...event, _rxAt: Date.now() }];
      return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next;
    });
  }, [event]);

  // Tick every 2 s so the gap fallback shows up after real silence.
  useEffect(() => {
    if (terminal) return;
    const iv = setInterval(() => setNow(Date.now()), 2000);
    return () => clearInterval(iv);
  }, [terminal]);

  const silentMs = now - lastRealAt.current;
  const showGap  = !terminal && silentMs >= GAP_MS && events.length > 0;

  // Fallback wording uses the LAST real phase — that's real context,
  // not a rotating placeholder.
  const gapLine = useMemo(() => {
    if (!showGap) return null;
    const lastPh = ((events[events.length - 1]?.phase) || "").toUpperCase();
    const label = PHASE_LABEL[lastPh] || (lastPh.toLowerCase() || "loop");
    const typical = PHASE_TYPICAL_S[lastPh];
    const hint = typical
      ? `usually ${typical - 5}-${typical + 10}s for repos this size`
      : "may take a moment";
    return `~ ${label} in progress — ${hint}`;
  }, [showGap, events]);

  if (!loopId || events.length === 0) return null;

  return (
    <div
      data-testid="loop-live-feed"
      style={{
        background: "#0F0F10",
        border:     "1px solid #ffffff14",
        borderRadius: 8,
        padding: "10px 12px",
        margin: "8px 0",
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        fontSize: 11.5,
        color: "#c9cbcf",
        maxHeight: 190,
        overflowY: "auto",
      }}>
      <div style={{
          display: "flex", alignItems: "center", gap: 6,
          fontSize: 10, letterSpacing: ".08em",
          color: "#6b7280", marginBottom: 6,
          textTransform: "uppercase",
        }}>
        <span style={{
          width: 6, height: 6, borderRadius: "50%",
          background: terminal ? "#22c55e" : "#FF6608",
          boxShadow: terminal ? "none" : "0 0 8px #FF660888",
          animation: terminal ? "none" : "loop-pulse 1.4s ease-in-out infinite",
        }} />
        Loop {String(loopId).slice(0, 8)}  ·  live feed
        <span style={{ marginLeft: "auto", color: "#4a5058" }}>
          last {events.length} event{events.length === 1 ? "" : "s"}
        </span>
      </div>

      {events.map((ev, i) => {
        const { tag, text, keepalive } = formatEventLine(ev);
        return (
          <div key={i}
                data-testid={`loop-live-event-${i}`}
                data-keepalive={keepalive ? "true" : undefined}
                style={{ display: "flex", gap: 8, alignItems: "baseline",
                          marginBottom: 3, lineHeight: 1.45,
                          // Iter 278 — heartbeats visually recede so
                          // they don't compete with real progress.
                          opacity: keepalive ? 0.55 : 1,
                          fontStyle: keepalive ? "italic" : "normal" }}>
            <span style={{
                color: keepalive ? "#6b7280" : phaseColor(ev.phase),
                fontWeight: keepalive ? 400 : 600,
                minWidth: 84, textAlign: "right",
              }}>
              {tag}
            </span>
            <span style={{ color: "#9aa0a8", flexShrink: 0 }}>—</span>
            <span style={{ flex: 1 }}>{text || "…"}</span>
          </div>
        );
      })}

      {gapLine && (
        <div data-testid="loop-live-gap"
              style={{
                marginTop: 4, color: "#5f6570",
                fontStyle: "italic", fontSize: 11,
              }}>
          {gapLine}
        </div>
      )}

      <style>{`
        @keyframes loop-pulse {
          0%,100% { opacity: 1;   transform: scale(1);   }
          50%     { opacity: 0.5; transform: scale(1.2); }
        }
      `}</style>
    </div>
  );
}
