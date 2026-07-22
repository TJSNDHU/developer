/**
 * LoopLiveFeedDemo — /dev/loop-live-feed
 *
 * Renders BOTH loop-run surfaces simultaneously with a single shared
 * event stream, mirroring how ChatPanel.jsx wires them in production:
 *
 *   Surface 1: growing assistant bubble in the message thread
 *              (matches ChatPanel::appendLoopBubble + renderEventLine)
 *   Surface 2: composer-adjacent <LoopLiveFeed /> panel  (Iter 275)
 *
 * Event shape is identical to `loop_engine.py::_new_event`. Only
 * the source is mocked (no OAuth/repo trigger available in this
 * environment) — everything downstream is the real code path.
 */
import React, { useEffect, useState } from "react";
import LoopLiveFeed from "../components/LoopLiveFeed";

const DEMO_EVENTS = [
  { phase: "plan",    state: "planning",  message: "Reading brief, drafting the fix plan" },
  { phase: "plan",    state: "planning",  message: "Plan approved — 3 files to change" },
  { phase: "execute", state: "executing", message: "Editing components/BookingCTA.tsx" },
  { phase: "execute", state: "executing", message: "Editing lib/hooks/useBooking.ts" },
  { phase: "verify",  state: "verifying", message: "Independent verifier: verdict yes" },
  { phase: "scan",    state: "scanning",  message: "Vanguard: 0 critical, 1 low finding" },
  { phase: "ship",    state: "shipping",  message: "Pushing commit to main" },
  { phase: "ship",    state: "completed", message: "Shipped 3 files — commit a1b2c3d",
    data: { commit_sha: "a1b2c3d" } },
];

// Exact clone of ChatPanel::renderEventLine — proving the growing
// bubble uses the same event-shape → line transform in production.
function renderEventLine(ev) {
  const ph = (ev.phase || "").toUpperCase();
  const st = ev.state || "";
  const ms = ev.message || "";
  if (st === "completed") return `**Step 5 / 5 — Ship**  ${ms}`;
  if (st === "failed")    return `**Failed**  ${ms}`;
  if (st === "aborted")   return `**Aborted**  ${ms}`;
  if (ph === "PLAN")      return `**Step 1 / 5 — Plan**  ${ms}`;
  if (ph === "EXECUTE")   return `**Step 2 / 5 — Execute**  ${ms}`;
  if (ph === "VERIFY")    return `**Step 3 / 5 — Verify**  ${ms}`;
  if (ph === "SCAN")      return `**Step 4 / 5 — Security**  ${ms}`;
  if (ph === "SHIP")      return `**Step 5 / 5 — Ship**  ${ms}`;
  return ms ? `· ${ms}` : "";
}

// Phase → real per-phase durations from the seeded iter275_demo_5phase
// audit rows. Sourced from /api/aurem-dev/ora-chat/slash /loop-stats.
const REAL_DURATIONS_S = {
  plan: 14.0, execute: 34.0, verify: 31.0, scan: 16.0, ship: 13.0,
};

export default function LoopLiveFeedDemo() {
  const [event, setEvent] = useState(null);
  const [terminal, setTerminal] = useState(false);
  const [i, setI] = useState(0);
  const [bubbleLines, setBubbleLines] = useState([]);

  useEffect(() => {
    if (i >= DEMO_EVENTS.length) { setTerminal(true); return; }
    const t = setTimeout(() => {
      const ev = { ...DEMO_EVENTS[i], ts: Date.now() / 1000 };
      setEvent(ev);
      const line = renderEventLine(ev);
      if (line) setBubbleLines((prev) => [...prev, line]);
      setI(i + 1);
    }, i === 0 ? 250 : 850);
    return () => clearTimeout(t);
  }, [i]);

  return (
    <div style={{
        minHeight: "100vh", background: "#0A0A0A", color: "#e5e7eb",
        padding: "28px 24px", fontFamily: "system-ui, sans-serif",
    }}>
      <div style={{ maxWidth: 780, margin: "0 auto" }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 6 }}>
          LoopLiveFeed + growing chat bubble — coexistence demo
        </h1>
        <p style={{ fontSize: 12, color: "#6b7280", marginBottom: 18 }}>
          Both surfaces driven by ONE event stream — identical to
          ChatPanel.jsx production wiring. Real per-phase durations
          from <code style={{ background: "#1a1a1a", padding: "0 4px",
                                borderRadius: 3 }}>/loop-stats
          iter275_demo_5phase</code>: plan 14s · execute 34s ·
          verify 31s · scan 16s · ship 13s · total 108s.
        </p>

        {/* ─── SURFACE 1 — Growing chat bubble in the message thread ─── */}
        <div style={{ marginBottom: 22 }}>
          <div style={{ fontSize: 11, letterSpacing: ".08em",
                          color: "#4a5058", marginBottom: 8,
                          textTransform: "uppercase" }}>
            Surface 1 · Message thread (growing assistant bubble)
          </div>
          <div style={{
              background: "#141414", borderRadius: 10,
              padding: "12px 14px", fontSize: 13, lineHeight: 1.55,
              border: "1px solid #ffffff10",
          }}>
            <div style={{ fontSize: 10, color: "#4a5058",
                            marginBottom: 6, textTransform: "uppercase",
                            letterSpacing: ".08em" }}>
              ● ORA · streaming
            </div>
            {bubbleLines.length === 0 && (
              <div style={{ color: "#4a5058" }}>Waiting for the loop…</div>
            )}
            {bubbleLines.map((ln, k) => (
              <div key={k} style={{ marginBottom: 4 }}
                   dangerouslySetInnerHTML={{
                     __html: ln.replace(
                       /\*\*(.+?)\*\*/g,
                       '<strong style="color:#e5e7eb">$1</strong>'
                     ),
                   }} />
            ))}
          </div>
        </div>

        {/* ─── SURFACE 2 — LoopLiveFeed panel above the composer ─── */}
        <div>
          <div style={{ fontSize: 11, letterSpacing: ".08em",
                          color: "#4a5058", marginBottom: 8,
                          textTransform: "uppercase" }}>
            Surface 2 · Composer-adjacent panel (Iter 275)
          </div>
          <LoopLiveFeed
            loopId="iter275_demo_5phase"
            event={event}
            terminal={terminal}
          />
        </div>

        {/* Fake composer for visual context */}
        <div style={{
            marginTop: 8, background: "#141414",
            border: "1px solid #ffffff10", borderRadius: 10,
            padding: "10px 14px", fontSize: 13, color: "#4a5058",
        }}>
          Type a follow-up (composer disabled during active loop)…
        </div>

        <p style={{ fontSize: 10.5, color: "#4a5058", marginTop: 20,
                    fontFamily: "'JetBrains Mono', monospace" }}>
          {terminal
            ? `run complete · ${DEMO_EVENTS.length} events emitted · both surfaces show terminal state`
            : `emitted ${i} / ${DEMO_EVENTS.length} events · both surfaces updating live`}
        </p>
      </div>
    </div>
  );
}
