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
    expect(screen.getByText(/Waiting for plan approval/i))
      .toBeInTheDocument();
  });

  it("clears-stale-prior-state: terminal=true purges heartbeat events from the ring buffer (iter288 regression)", () => {
    // Feed a heartbeat event first — it should render.
    const heartbeat = {
      state:  "executing",
      phase:  "execute",
      data:   { sub_step: "heartbeat", keepalive: true,
                 message: "Still waiting on LLM response for foo.py" },
    };
    const { rerender } = render(
      <LoopLiveFeed loopId="l1" event={heartbeat} terminal={false} />
    );
    // Sanity — feed rendered SOMETHING (either as a live event or
    // the placeholder if the heartbeat filter suppressed it).
    expect(screen.getByTestId("loop-live-feed")).toBeInTheDocument();

    // Now a terminal event arrives. Push a NEW heartbeat first (to
    // simulate a late in-flight frame) then flip terminal true.
    rerender(<LoopLiveFeed loopId="l1" event={heartbeat} terminal={false} />);
    // Terminal flag flips true — the useEffect purge must fire and
    // strip heartbeat/keepalive entries from the events ring buffer.
    act(() => {
      rerender(<LoopLiveFeed loopId="l1" event={null} terminal={true} />);
    });
    // After purge, the heartbeat "Still waiting on LLM response..."
    // line must NOT appear. If it does, the ring buffer wasn't
    // filtered — the iter288 fix has regressed.
    expect(screen.queryByText(/Still waiting on LLM response/i)).toBeNull();
    // And the gap-fallback line (~ Execute in progress...) must not
    // render either, since terminal=true short-circuits it.
    expect(screen.queryByTestId("loop-live-gap")).toBeNull();
  });

  it("race-condition: a late heartbeat delivered AFTER terminal=true does not re-appear in the rendered feed", () => {
    // Sequence: terminal fires first, then a stale heartbeat is
    // handed to the component (this is the real race iter288 fought
    // — a per-file Parliament heartbeat whose queue.put awaited
    // across _fail's own _emit). LoopLiveFeed's purge must keep the
    // buffer clean even if a subsequent late frame lands.
    const late = {
      state:  "executing",
      phase:  "execute",
      data:   { sub_step: "heartbeat", keepalive: true,
                 message: "LATE HEARTBEAT MUST NOT SHOW" },
    };
    const { rerender } = render(
      <LoopLiveFeed loopId="l2" event={null} terminal={true} />
    );
    // Now the late heartbeat arrives — component receives new
    // `event` prop. The purge effect should still keep it out
    // because the ring buffer was already emptied and terminal
    // is still true.
    act(() => {
      rerender(<LoopLiveFeed loopId="l2" event={late} terminal={true} />);
    });
    // The stale-late text must NOT be in the DOM.
    expect(screen.queryByText(/LATE HEARTBEAT MUST NOT SHOW/)).toBeNull();
    // NB: this test also documents the invariant contract — a
    // future refactor that stops honouring `terminal` in the purge
    // effect (e.g. someone drops the `if (!terminal) return`)
    // will fail here loudly.
  });
});
