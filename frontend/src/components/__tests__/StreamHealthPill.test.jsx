/**
 * StreamHealthPill.test.jsx — Iter 302 (Frontend QA Charter Layer 1 audit)
 *
 * State-sync behavior tests — same 3-test template as
 * LoopStepBar.test.jsx (iter294) / AgentStatusBar.test.jsx (iter295) /
 * IntentTierIndicator.test.jsx (iter296).
 *
 * StreamHealthPill is the tiny status pill that surfaces when the
 * `/chat/stream` SSE stalls — driven purely by the `state.phase`
 * prop (idle / slow / reconnecting). The invariants this file locks:
 *
 *   1. `state.phase === "slow"` renders the amber "Slow response" copy
 *      with silence duration; `state.phase === "reconnecting"` swaps
 *      the copy to red "Reconnecting" AND flips the data-stream-phase
 *      attribute in the SAME render (state-sync bug class).
 *   2. Transition back to `idle` in a same-instance rerender REMOVES
 *      the pill entirely — no stale "Slow response" text left behind.
 *   3. Race-condition: `state.phase === "idle"` (or nullish state)
 *      returns null unconditionally; NO combination of other state
 *      fields can force the pill to render.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import StreamHealthPill from "../chat/StreamHealthPill.jsx";


describe("StreamHealthPill — state-sync behavior (iter302)", () => {
  it("reaches-correct-terminal-state: slow → reconnecting swaps copy AND data-stream-phase in one render", () => {
    const { rerender } = render(
      <StreamHealthPill state={{ phase: "slow", silentFor: 8, retryEtaSec: 4 }} />
    );
    // Slow branch renders amber "Slow response" copy.
    const pill = screen.getByTestId("chat-stream-health-pill");
    expect(pill.getAttribute("data-stream-phase")).toBe("slow");
    expect(pill.textContent).toMatch(/slow response/i);
    expect(pill.textContent).toContain("8s silent");
    expect(pill.textContent).toMatch(/auto-retry in 4s/i);

    // Real event: reconnect starts. Copy AND attribute must flip in
    // the SAME render — the exact stale-state bug class the charter
    // was written to catch.
    rerender(
      <StreamHealthPill state={{ phase: "reconnecting", silentFor: 12 }} />
    );
    const pill2 = screen.getByTestId("chat-stream-health-pill");
    expect(pill2.getAttribute("data-stream-phase")).toBe("reconnecting");
    expect(pill2.textContent).toMatch(/reconnecting/i);
    // The stale "Slow response" text must be GONE from the DOM.
    expect(screen.queryByText(/slow response/i)).toBeNull();
    // The stale "auto-retry" copy also gone (only rendered in slow).
    expect(screen.queryByText(/auto-retry/i)).toBeNull();
  });

  it("clears-stale-prior-state: transitioning to phase='idle' removes the pill entirely", () => {
    const { rerender, container } = render(
      <StreamHealthPill state={{ phase: "slow", silentFor: 5 }} />
    );
    expect(screen.getByTestId("chat-stream-health-pill")).toBeInTheDocument();

    // Stream recovered — engine sets phase=idle. Pill MUST unmount;
    // no stale amber banner may linger.
    rerender(<StreamHealthPill state={{ phase: "idle" }} />);
    expect(screen.queryByTestId("chat-stream-health-pill")).toBeNull();
    expect(screen.queryByText(/slow response/i)).toBeNull();
    expect(container.firstChild).toBeNull();
  });

  it("race-condition: idle/null state is the sole gate — no other prop combination forces render", () => {
    // If a jailbroken state carries loud fields (silentFor, retryEtaSec)
    // but phase is idle/null, the pill MUST stay unmounted. Guards
    // against a future "OR-fallback" refactor that would render on
    // any truthy field.
    const { container: c1 } = render(
      <StreamHealthPill state={{ phase: "idle", silentFor: 99, retryEtaSec: 99 }} />
    );
    expect(c1.firstChild).toBeNull();

    const { container: c2 } = render(<StreamHealthPill state={null} />);
    expect(c2.firstChild).toBeNull();

    const { container: c3 } = render(<StreamHealthPill state={undefined} />);
    expect(c3.firstChild).toBeNull();
  });

  it("retry-now button fires the onRetry callback exactly once, only when provided", () => {
    // Additional invariant lock: the Retry button MUST wire straight
    // to onRetry — never crossed to onClose or a global toast.
    const onRetry = vi.fn();
    render(
      <StreamHealthPill
        state={{ phase: "reconnecting", silentFor: 30 }}
        onRetry={onRetry}
      />
    );
    fireEvent.click(screen.getByTestId("chat-stream-retry-now"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
