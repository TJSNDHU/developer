/**
 * LoopLiveFeed.test.jsx — Iter 295 (Frontend Layer 1, Batch 1)
 *
 * State-sync behavior tests. Covers 3 bug classes already found:
 *   1. iter281 — never return null while loopId is set
 *      (placeholder must render even before first SSE event).
 *   2. iter288 — terminal event purges heartbeat entries from the
 *      ring buffer.
 *   3. race-condition — a stale heartbeat arriving AFTER a
 *      terminal event does NOT re-appear in the rendered list.
 *
 * All 3 tests query the rendered DOM via `screen` / `container` —
 * no props, no internal state, no source-string grep.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, act } from "@testing-library/react";
import LoopLiveFeed from "../LoopLiveFeed.jsx";


describe("LoopLiveFeed — state-sync behavior (iter295)", () => {
  it("reaches-correct-terminal-state: renders pending-placeholder while loopId is set but no events yet (iter281 regression)", () => {
    render(<LoopLiveFeed loopId="abc123" event={null} terminal={false} />);
    // Panel must be visible.
    const panel = screen.getByTestId("loop-live-feed");
    expect(panel).toBeInTheDocument();
    expect(panel.getAttribute("data-state")).toBe("pending");
    // Placeholder line must render (iter281's exact bug).
    expect(screen.getByTestId("loop-live-feed-placeholder"))
      .toBeInTheDocument();
    expect(screen.getByText(/Opening event stream/i))
      .toBeInTheDocument();
  });

  it("iter309 · Item A: heartbeat events NEVER render as visible feed lines", () => {
    // Heartbeat frames still arrive from the backend to keep the SSE
    // connection alive, but Iter 309 removed their visual rendering.
    // The active-step ECG waveform (Part 2) already conveys "still
    // working, no new data". This test guards Item A.
    const heartbeat = {
      state:  "executing",
      phase:  "execute",
      data:   { sub_step: "heartbeat", keepalive: true,
                 message: "Still waiting on LLM response for foo.py" },
    };
    const { rerender } = render(
      <LoopLiveFeed loopId="l1" event={heartbeat} terminal={false} />
    );
    // Feed still renders — but heartbeat text must not be visible.
    expect(screen.getByTestId("loop-live-feed")).toBeInTheDocument();
    expect(screen.queryByText(/Still waiting on LLM response/i)).toBeNull();

    // Also — no gap-fallback line is ever rendered (Item B).
    rerender(<LoopLiveFeed loopId="l1" event={null} terminal={false} />);
    expect(screen.queryByTestId("loop-live-gap")).toBeNull();
  });

  it("iter309 · Item A: late heartbeat delivered AFTER terminal never appears", () => {
    // The invariant is now stronger — heartbeats never render at all,
    // so there's nothing to "purge on terminal". This test documents
    // that a late heartbeat post-terminal still produces no visible
    // text row (matches the Item A contract exactly).
    const late = {
      state:  "executing",
      phase:  "execute",
      data:   { sub_step: "heartbeat", keepalive: true,
                 message: "LATE HEARTBEAT MUST NOT SHOW" },
    };
    const { rerender } = render(
      <LoopLiveFeed loopId="l2" event={null} terminal={true} />
    );
    act(() => {
      rerender(<LoopLiveFeed loopId="l2" event={late} terminal={true} />);
    });
    expect(screen.queryByText(/LATE HEARTBEAT MUST NOT SHOW/)).toBeNull();
  });
});
