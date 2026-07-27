/**
 * LoopLiveFeed.iter329_stale_spinner.test.jsx
 *
 * Iter 329 · Fix B — stale-spinner-on-terminal regression lock-in.
 *
 * Live bug (confirmed on commit 1f70444 ship-success path):
 *   Founder shipped a real commit. Top status went to SHIPPED. The
 *   live feed KEPT rendering:
 *     "Writing… (stalled)"       ← red icon, spinner active
 *     "Running scan… (stalled)"  ← red icon, spinner active
 *   The correlation_id-matching resolver frames never landed (SSE gap
 *   OR backend narration omission), so the pending lines stayed
 *   pending past the >60s stall threshold.
 *
 * Fix: at RENDER LAYER, once `terminal=true`, every still-pending
 * line is force-resolved based on the loop's terminal state.
 *
 * Invariants locked in below:
 *   1. Ship-success terminal (state=completed) → pending → success
 *      • No spinner
 *      • No timer
 *      • No `(stalled)` badge
 *      • data-tone="success" on the line
 *   2. Abort terminal (state=aborted) → pending → warning
 *   3. Failed terminal (state=failed) → pending → danger
 *   4. Non-terminal (terminal=false) → nothing rewritten, pending
 *      lines still render as pending (behavior unchanged).
 *   5. Pure helper: `resolvePendingOnTerminal` marks resolved lines
 *      with `__resolvedOnTerminal: true` (test hook).
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, act } from "@testing-library/react";
import LoopLiveFeed, {
  foldNarrations,
  resolveTerminalTone,
  resolvePendingOnTerminal,
} from "../LoopLiveFeed.jsx";


// Build a narration SSE event of the shape LoopLiveFeed consumes.
function narration({ text, corr, tone = "pending", tsEpoch, state }) {
  return {
    state: state,   // top-level state field — resolveTerminalTone reads this
    data: {
      type: "narration",
      tone,
      narration_step: "execute",
      narration_text: text,
      correlation_id: corr,
      ts_epoch: tsEpoch,
    },
  };
}

// Render then force a microtask + effect flush so the append-events
// useEffect commits before assertions. Two useEffects fire on mount
// in LoopLiveFeed:
//   1. `useEffect([event], setEvents(prev => [...prev, event]))` — append
//   2. `useEffect([loopId], setEvents([]))`                       — clear
// Both fire on the same mount tick. React batches them; the CLEAR
// runs LAST in source order and wipes the append. So we must mount
// with event=null first, then rerender with the actual event so the
// clear-on-loopId-change effect has already committed.
function renderFeed(props) {
  const { event, ...rest } = props;
  let handle;
  act(() => {
    handle = render(<LoopLiveFeed {...rest} event={null} />);
  });
  if (event) {
    act(() => { handle.rerender(<LoopLiveFeed {...rest} event={event} />); });
  }
  return handle;
}
function rerenderFeed(handle, props) {
  act(() => { handle.rerender(<LoopLiveFeed {...props} />); });
}


describe("Iter 329 · Fix B — stale spinner resolves on terminal", () => {
  it("resolveTerminalTone maps state=completed → success", () => {
    const events = [narration({ text: "x", corr: "c1", state: "completed" })];
    expect(resolveTerminalTone(events)).toBe("success");
  });

  it("resolveTerminalTone maps state=failed → danger", () => {
    const events = [narration({ text: "x", corr: "c1", state: "failed" })];
    expect(resolveTerminalTone(events)).toBe("danger");
  });

  it("resolveTerminalTone maps state=aborted → warning", () => {
    const events = [narration({ text: "x", corr: "c1", state: "aborted" })];
    expect(resolveTerminalTone(events)).toBe("warning");
  });

  it("resolveTerminalTone falls back to success when no state present", () => {
    const events = [narration({ text: "x", corr: "c1" })];  // no state
    expect(resolveTerminalTone(events)).toBe("success");
  });

  it("resolvePendingOnTerminal rewrites ONLY pending lines when terminal=true", () => {
    const folded = [
      { key: "a", tone: "pending", text: "Writing…",     tsEpoch: 100 },
      { key: "b", tone: "success", text: "Plan approved", tsEpoch: 90 },
      { key: "c", tone: "pending", text: "Running scan…", tsEpoch: 110 },
    ];
    const out = resolvePendingOnTerminal(folded, true, "success");
    expect(out[0].tone).toBe("success");
    expect(out[0].__resolvedOnTerminal).toBe(true);
    expect(out[1].tone).toBe("success");         // unchanged
    expect(out[1].__resolvedOnTerminal).toBeUndefined();
    expect(out[2].tone).toBe("success");
    expect(out[2].__resolvedOnTerminal).toBe(true);
  });

  it("resolvePendingOnTerminal is a no-op when terminal=false (regression guard)", () => {
    const folded = [
      { key: "a", tone: "pending", text: "Writing…", tsEpoch: 100 },
    ];
    const out = resolvePendingOnTerminal(folded, false, "success");
    expect(out[0].tone).toBe("pending");
    expect(out[0].__resolvedOnTerminal).toBeUndefined();
  });

  // ── Live-DOM tests exercising the ChatPanel → LoopLiveFeed contract ─

  function renderWithShipSuccess() {
    // Simulate the exact bug shape: two pending narrations arrive,
    // then a terminal COMPLETED event lands, but NO resolver frames
    // ever come. This is what commit 1f70444 produced live.
    const t0 = Math.floor(Date.now() / 1000) - 120;  // 2 min ago
    const shipSuccess = {
      state: "completed", phase: "ship",
      data: { type: "state", commit_sha: "1f70444" },
    };
    const handle = renderFeed({
      loopId: "loop_iter329_test",
      event: narration({ text: "Writing files…", corr: "c-write", tsEpoch: t0 }),
      terminal: false,
    });
    rerenderFeed(handle, {
      loopId: "loop_iter329_test",
      event: narration({ text: "Running scan…", corr: "c-scan", tsEpoch: t0 + 5 }),
      terminal: false,
    });
    // Terminal COMPLETED lands.
    rerenderFeed(handle, {
      loopId: "loop_iter329_test",
      event: shipSuccess,
      terminal: true,
    });
    return { handle, shipSuccess };
  }

  it("SHIP-SUCCESS path: two never-resolved pending lines flip to success + no stalled badge (real-bug lock-in)", () => {
    renderWithShipSuccess();

    // Both pending lines must render — but as SUCCESS, not pending.
    const lineWrite = screen.getByTestId("loop-narration-line-c-write");
    const lineScan  = screen.getByTestId("loop-narration-line-c-scan");
    expect(lineWrite.getAttribute("data-tone")).toBe("success");
    expect(lineScan.getAttribute("data-tone")).toBe("success");

    // No stalled badge on either line — the exact visual founder
    // complained about ("Writing… (stalled)").
    expect(screen.queryByTestId("loop-narration-stalled-c-write")).toBeNull();
    expect(screen.queryByTestId("loop-narration-stalled-c-scan")).toBeNull();

    // No live-ticking timer — timer only renders while tone=pending.
    expect(screen.queryByTestId("loop-narration-timer-c-write")).toBeNull();
    expect(screen.queryByTestId("loop-narration-timer-c-scan")).toBeNull();

    // data-stalled must be false on both.
    expect(lineWrite.getAttribute("data-stalled")).toBe("false");
    expect(lineScan.getAttribute("data-stalled")).toBe("false");
  });

  it("ABORT path: pending lines flip to warning tone", () => {
    const t0 = Math.floor(Date.now() / 1000) - 30;
    const handle = renderFeed({
      loopId: "loop_abort_test",
      event: narration({ text: "Planning…", corr: "c-plan", tsEpoch: t0 }),
      terminal: false,
    });
    // User hits abort — terminal ABORTED lands.
    rerenderFeed(handle, {
      loopId: "loop_abort_test",
      event: { state: "aborted", data: { type: "state" } },
      terminal: true,
    });
    const line = screen.getByTestId("loop-narration-line-c-plan");
    expect(line.getAttribute("data-tone")).toBe("warning");
    expect(screen.queryByTestId("loop-narration-timer-c-plan")).toBeNull();
    expect(screen.queryByTestId("loop-narration-stalled-c-plan")).toBeNull();
  });

  it("FAILED path: pending lines flip to danger tone", () => {
    const t0 = Math.floor(Date.now() / 1000) - 30;
    const handle = renderFeed({
      loopId: "loop_fail_test",
      event: narration({ text: "Executing…", corr: "c-exec", tsEpoch: t0 }),
      terminal: false,
    });
    rerenderFeed(handle, {
      loopId: "loop_fail_test",
      event: { state: "failed", data: { type: "state" } },
      terminal: true,
    });
    const line = screen.getByTestId("loop-narration-line-c-exec");
    expect(line.getAttribute("data-tone")).toBe("danger");
    expect(screen.queryByTestId("loop-narration-timer-c-exec")).toBeNull();
  });

  it("NON-TERMINAL: pending lines stay pending + timer keeps rendering (regression guard)", () => {
    const t0 = Math.floor(Date.now() / 1000) - 5;
    renderFeed({
      loopId: "loop_running",
      event: narration({ text: "Working…", corr: "c-work", tsEpoch: t0 }),
      terminal: false,
    });
    const line = screen.getByTestId("loop-narration-line-c-work");
    expect(line.getAttribute("data-tone")).toBe("pending");
    expect(screen.getByTestId("loop-narration-timer-c-work")).toBeInTheDocument();
  });
});
