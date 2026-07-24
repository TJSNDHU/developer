/**
 * AgentStatusBar.test.jsx — Iter 295 (Frontend Layer 1, Batch 1)
 *
 * State-sync behavior tests — same 3-test template as
 * LoopStepBar.test.jsx (iter294). Verified BEHAVIOURAL by iter290
 * classifier; no source-string grep, all DOM queries.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import AgentStatusBar from "../AgentStatusBar.jsx";


describe("AgentStatusBar — state-sync behavior (iter295)", () => {
  it("reaches-correct-terminal-state: busy=true renders the bar, busy=false removes it", () => {
    const { rerender } = render(<AgentStatusBar busy={true} queuedCount={0} />);
    expect(screen.getByTestId("agent-status-bar")).toBeInTheDocument();
    expect(screen.getByText(/Agent is running/i)).toBeInTheDocument();

    // Terminal event flips busy false — bar MUST disappear from DOM
    // in the SAME render, not on the next tick. This is the iter288
    // bug: the bar persisted past a failed terminal event.
    rerender(<AgentStatusBar busy={false} queuedCount={0} />);
    expect(screen.queryByTestId("agent-status-bar")).toBeNull();
    expect(screen.queryByText(/Agent is running/i)).toBeNull();
  });

  it("clears-stale-prior-state: queuedCount chip only appears when count > 0", () => {
    // With queuedCount=0 the chip must NOT render even while busy.
    const { rerender } = render(<AgentStatusBar busy={true} queuedCount={0} />);
    expect(screen.queryByTestId("queued-chip")).toBeNull();

    // Then a queued message arrives — chip appears with the count.
    rerender(<AgentStatusBar busy={true} queuedCount={3} />);
    const chip = screen.getByTestId("queued-chip");
    expect(chip.textContent).toContain("3");
    expect(chip.textContent).toContain("queued");

    // Terminal event clears busy — BOTH the bar AND the chip must
    // vanish, regardless of the stale queuedCount value. If chip
    // rendered outside the busy guard, this catches it.
    rerender(<AgentStatusBar busy={false} queuedCount={3} />);
    expect(screen.queryByTestId("agent-status-bar")).toBeNull();
    expect(screen.queryByTestId("queued-chip")).toBeNull();
  });

  it("race-condition: busy=false is the sole gate — no prop combination can force the bar to render", () => {
    // The bar's visibility invariant: busy===false ⇒ nothing renders,
    // no matter what queuedCount / other props are. This is what
    // ChatPanel's handleLoopEvent (iter288) relies on: it calls
    // setBusy(false) on terminal, and trusts this component to
    // vanish immediately. If a future refactor added an "OR-fallback"
    // that rendered the bar based on queuedCount alone, THIS test
    // catches it.
    const { container } = render(
      <AgentStatusBar busy={false} queuedCount={99} />
    );
    expect(screen.queryByTestId("agent-status-bar")).toBeNull();
    expect(screen.queryByTestId("agent-status-shell")).toBeNull();
    expect(screen.queryByTestId("queued-chip")).toBeNull();
    // The whole component returned null — no children at all.
    expect(container.firstChild).toBeNull();
  });
});
