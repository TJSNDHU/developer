/**
 * SelfHealIndicator.test.jsx — Iter 296 (Frontend Layer 1, Batch 2)
 *
 * State-sync behavior tests — same 3-test template as
 * LoopStepBar.test.jsx (iter294) / AgentStatusBar.test.jsx (iter295).
 *
 * SelfHealIndicator is a slim inline strip that lights up while the
 * loop engine is rewriting a file that failed ruff/eslint. It is
 * gated purely by `visible` — an early `return null` bypasses every
 * other prop. This test file locks that invariant so a future
 * refactor cannot introduce an "OR-fallback" that renders the strip
 * from any other prop combination (the same failure class as the
 * iter288 AgentStatusBar bug).
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SelfHealIndicator } from "../LoopActionCards.jsx";


describe("SelfHealIndicator — state-sync behavior (iter296)", () => {
  it("reaches-correct-terminal-state: visible=true renders the strip with attempt copy, visible=false removes it", () => {
    const { rerender } = render(
      <SelfHealIndicator visible={true} attempt={2} max={3} />
    );
    const strip = screen.getByTestId("self-heal-indicator");
    expect(strip).toBeInTheDocument();
    // The strip is a role=status live region — screen readers must
    // announce the "attempt N of M" phrase. Copy MUST include both
    // numbers, not just one.
    expect(strip.textContent).toMatch(/attempt/i);
    expect(strip.textContent).toContain("2");
    expect(strip.textContent).toContain("3");

    // Terminal event — the loop finished (or moved off self-heal) —
    // visible flips false. The strip MUST disappear from the DOM in
    // the same render, not the next tick. That's the exact iter288
    // failure mode we're locking against.
    rerender(<SelfHealIndicator visible={false} attempt={2} max={3} />);
    expect(screen.queryByTestId("self-heal-indicator")).toBeNull();
    expect(screen.queryByText(/attempt/i)).toBeNull();
  });

  it("clears-stale-prior-state: attempt rerender updates the copy, then visible=false clears the strip regardless of stale attempt", () => {
    const { rerender } = render(
      <SelfHealIndicator visible={true} attempt={1} max={3}
                          errorPreview="ruff: E501" />
    );
    // First render — attempt 1 shows, errorPreview code block shows.
    let strip = screen.getByTestId("self-heal-indicator");
    expect(strip.textContent).toContain("1");
    expect(strip.textContent).toContain("ruff: E501");

    // Engine moves to attempt 3 — copy must reflect it in the SAME
    // instance (rerender, not remount). If the parent forgot to
    // pass the new prop through, the strip would still read "1".
    rerender(
      <SelfHealIndicator visible={true} attempt={3} max={3}
                          errorPreview="ruff: E501" />
    );
    strip = screen.getByTestId("self-heal-indicator");
    expect(strip.textContent).toContain("3");
    // The old "1" must not remain — assert on the strong tag copy
    // specifically to avoid matching the digit inside "3/3".
    // (The <strong> renders literally "3/3" so "1" is truly gone.)
    expect(strip.textContent).not.toMatch(/attempt\s*1\/3/i);

    // Self-heal succeeded — visible=false. The strip must vanish
    // even though `attempt` is still 3 (stale from the parent). The
    // gate is EXCLUSIVELY visible — no OR-fallback allowed.
    rerender(
      <SelfHealIndicator visible={false} attempt={3} max={3}
                          errorPreview="ruff: E501" />
    );
    expect(screen.queryByTestId("self-heal-indicator")).toBeNull();
    expect(screen.queryByText(/ruff: E501/)).toBeNull();
  });

  it("race-condition: visible=false is the sole gate — no other prop combination can force the strip to render", () => {
    // The invariant: when visible is false, the component returns
    // null — no matter how loud the other props are. If someone
    // later replaces `if (!visible) return null` with a truthy-any
    // OR chain, THIS test fails loudly.
    const { container } = render(
      <SelfHealIndicator visible={false} attempt={99} max={3}
                          errorPreview="critical: everything on fire" />
    );
    expect(screen.queryByTestId("self-heal-indicator")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByText(/attempt/i)).toBeNull();
    expect(screen.queryByText(/critical: everything on fire/)).toBeNull();
    // The whole component returned null — the container is empty.
    expect(container.firstChild).toBeNull();
  });
});
