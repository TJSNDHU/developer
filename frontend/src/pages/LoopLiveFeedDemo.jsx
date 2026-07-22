/**
 * LoopLiveFeedDemo — /dev/loop-live-feed
 *
 * A transparent dev-only mount of the REAL LoopLiveFeed component.
 * Streams events that match the exact schema emitted by
 * backend/services/loop_engine.py::_new_event, sourced from the
 * seeded `iter275_demo_5phase` audit rows. The event shapes are
 * identical to what /loop/{id}/stream serves in production — this
 * is not a mock component, only a mocked event source.
 *
 * Existence: solely to produce a screenshot of the panel rendering
 * without needing an authenticated founder-connected repo to trigger
 * a real loop run.
 */
import React, { useEffect, useState } from "react";
import LoopLiveFeed from "../components/LoopLiveFeed";

const DEMO_EVENTS = [
  { phase: "plan",    state: "planning",   message: "Reading brief, drafting the fix plan" },
  { phase: "plan",    state: "planning",   message: "Plan approved — 3 files to change" },
  { phase: "execute", state: "executing",  message: "Editing components/BookingCTA.tsx" },
  { phase: "execute", state: "executing",  message: "Editing lib/hooks/useBooking.ts" },
  { phase: "verify",  state: "verifying",  message: "Independent verifier: verdict yes" },
  { phase: "scan",    state: "scanning",   message: "Vanguard: 0 critical, 1 low finding" },
  { phase: "ship",    state: "shipping",   message: "Pushing commit to main" },
  { phase: "ship",    state: "completed",  message: "Shipped 3 files — commit a1b2c3d",
    data: { commit_sha: "a1b2c3d" } },
];

export default function LoopLiveFeedDemo() {
  const [event, setEvent] = useState(null);
  const [terminal, setTerminal] = useState(false);
  const [i, setI] = useState(0);

  useEffect(() => {
    if (i >= DEMO_EVENTS.length) {
      setTerminal(true);
      return;
    }
    // Emit at a moderate pace so screenshots capture 4-5 events in the
    // ring buffer at any given moment.
    const t = setTimeout(() => {
      const ev = { ...DEMO_EVENTS[i], ts: Date.now() / 1000 };
      setEvent(ev);
      setI(i + 1);
    }, i === 0 ? 300 : 900);
    return () => clearTimeout(t);
  }, [i]);

  return (
    <div style={{
        minHeight: "100vh", background: "#0A0A0A", color: "#e5e7eb",
        padding: "40px 24px", fontFamily: "system-ui, sans-serif",
    }}>
      <div style={{ maxWidth: 720, margin: "0 auto" }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 6 }}>
          LoopLiveFeed — dev demo
        </h1>
        <p style={{ fontSize: 12.5, color: "#6b7280", marginBottom: 20 }}>
          Real component. Event shape identical to
          <code style={{ padding: "0 4px", background: "#1a1a1a",
                          borderRadius: 3 }}>
            loop_engine.py::_new_event
          </code>. Timeline: 5 phases → completed.
        </p>
        <LoopLiveFeed
          loopId="iter275_demo_5phase"
          event={event}
          terminal={terminal}
        />
        <p style={{ fontSize: 11, color: "#4a5058", marginTop: 16,
                    fontFamily: "'JetBrains Mono', monospace" }}>
          {terminal
            ? `run complete · ${DEMO_EVENTS.length} events emitted`
            : `emitted ${i} / ${DEMO_EVENTS.length} events…`}
        </p>
      </div>
    </div>
  );
}
