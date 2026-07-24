/**
 * VisualFixtures.jsx — /dev/visual — Iter 302 (Frontend QA Charter Layer 2 Batch 2)
 *
 * Fixture-driven isolation surface for Playwright's `toHaveScreenshot`.
 * Renders exactly ONE component in a known state per request, driven
 * by `?state=<name>` in the URL. No SSE, no auth, no backend calls —
 * fully hermetic, sub-100 ms first paint. This is the surface the
 * charter Layer 2 wanted: state-specific baselines for the phase
 * stepper (4 states) and LoopLiveFeed (3 states), driven by prop
 * injection so a snapshot captures the exact terminal-state visual.
 *
 * States (all documented in docs/visual_regression.md):
 *   Phase stepper: executing | completed | failed | paused_for_user
 *   LoopLiveFeed:  pending   | live-events | terminal
 *
 * The page renders on a stable dark background matching production
 * chat context so pixel diffs measure the COMPONENT, not the page
 * chrome around it.
 */
import React from "react";
import { useSearchParams } from "react-router-dom";
import LoopStepBar from "../components/LoopStepBar";
import LoopLiveFeed from "../components/LoopLiveFeed";


// Fixed reference timestamps (Unix epoch millis) so every snapshot
// captures the exact same "seconds ago" copy each run.
const FROZEN_TS = 1739000000_000;  // 2025-02-08T…UTC — arbitrary but frozen


function Stage({ label, children }) {
  return (
    <div
      data-testid="visual-fixture-stage"
      style={{
        minHeight: "100vh",
        background: "#0a0a0a",
        padding: "40px 24px",
        fontFamily: "'JetBrains Mono', monospace",
        color: "#e5e5e5",
      }}
    >
      <div style={{
        fontSize: 11,
        color: "#666",
        marginBottom: 16,
        letterSpacing: 0.5,
      }}>
        {label}
      </div>
      {/* Fixed-width container so the layout is deterministic across
          runs — no viewport-percentage math. 960 matches the composer
          column in production at 1440-viewport. */}
      <div style={{ maxWidth: 960, margin: "0 auto" }}>
        {children}
      </div>
    </div>
  );
}


// ── LoopStepBar fixtures ────────────────────────────────────────
function StepBarExecuting() {
  // phase="executing" → active step is EXECUTE (step 2 of 5).
  return (
    <Stage label="fixture: loop-step-bar / executing">
      <LoopStepBar phase="executing" retryCount={0} />
    </Stage>
  );
}
function StepBarCompleted() {
  // phase="done" → all 5 steps green check.
  return (
    <Stage label="fixture: loop-step-bar / completed">
      <LoopStepBar phase="done" retryCount={0} />
    </Stage>
  );
}
function StepBarFailed() {
  // phase="error" with errorStep=3 → VERIFY step red triangle.
  return (
    <Stage label="fixture: loop-step-bar / failed">
      <LoopStepBar phase="error" retryCount={2} errorStep={3} />
    </Stage>
  );
}
function StepBarPausedForUser() {
  // paused_for_user in production usually holds phase=verifying with
  // retryCount ticking. LoopStepBar treats the pause as "still on the
  // last active step" — active pill remains but no advance.
  return (
    <Stage label="fixture: loop-step-bar / paused_for_user">
      <LoopStepBar phase="verifying" retryCount={1} />
    </Stage>
  );
}


// ── LoopLiveFeed fixtures ───────────────────────────────────────
function FeedPending() {
  // No event, not terminal — LoopLiveFeed shows its pending
  // placeholder (iter281 fix).
  return (
    <Stage label="fixture: loop-live-feed / pending-placeholder">
      <LoopLiveFeed loopId="visual-fx" event={null} terminal={false} />
    </Stage>
  );
}
function FeedLiveEvents() {
  // A sequence of events fed via successive `event` props matches
  // the live-events state in production. To keep this fixture
  // deterministic, we render the feed then inject THREE events via
  // a tiny useEffect chain (LoopLiveFeed pushes into its own state
  // whenever `event` changes). We do this synchronously via
  // `React.useState` seeded via a controller.
  const [i, setI] = React.useState(3);   // pre-drive to the 3rd event
  const EVENTS = [
    { phase: "plan",    state: "planning",  message: "Reading brief, drafting the fix plan", ts: FROZEN_TS + 0 },
    { phase: "execute", state: "executing", message: "Generating routers/health.py",         ts: FROZEN_TS + 1000 },
    { phase: "verify",  state: "verifying", message: "Independent verifier: verdict yes",    ts: FROZEN_TS + 2000 },
  ];
  // Drive the feed by feeding each event once through the `event`
  // prop. `i` counts down synchronously.
  React.useEffect(() => {
    if (i > 0) setI(i - 1);
  }, [i]);
  return (
    <Stage label="fixture: loop-live-feed / live-events">
      <LoopLiveFeed
        loopId="visual-fx"
        event={EVENTS[3 - i] || EVENTS[EVENTS.length - 1]}
        terminal={false}
      />
    </Stage>
  );
}
function FeedTerminal() {
  return (
    <Stage label="fixture: loop-live-feed / terminal">
      <LoopLiveFeed
        loopId="visual-fx"
        event={{
          phase: "ship",
          state: "completed",
          message: "Loop finished — commit pushed to main",
          ts: FROZEN_TS + 5000,
        }}
        terminal={true}
      />
    </Stage>
  );
}


const FIXTURES = {
  "step-executing":        StepBarExecuting,
  "step-completed":        StepBarCompleted,
  "step-failed":           StepBarFailed,
  "step-paused-for-user":  StepBarPausedForUser,
  "feed-pending":          FeedPending,
  "feed-live-events":      FeedLiveEvents,
  "feed-terminal":         FeedTerminal,
};


export default function VisualFixtures() {
  const [params] = useSearchParams();
  const state = params.get("state") || "index";
  if (state === "index") {
    return (
      <Stage label="fixture: index">
        <ul data-testid="visual-fixture-index"
             style={{ listStyle: "none", padding: 0, lineHeight: 1.9 }}>
          {Object.keys(FIXTURES).map((s) => (
            <li key={s}>
              <a href={`/dev/visual?state=${s}`}
                  style={{ color: "#7dd3fc", textDecoration: "none" }}>
                /dev/visual?state={s}
              </a>
            </li>
          ))}
        </ul>
      </Stage>
    );
  }
  const F = FIXTURES[state];
  if (!F) {
    return (
      <Stage label={`fixture: UNKNOWN state=${state}`}>
        <div data-testid="visual-fixture-unknown">
          Unknown state: <code>{state}</code>. Known:
          {" " + Object.keys(FIXTURES).join(", ")}
        </div>
      </Stage>
    );
  }
  return <F />;
}
