/**
 * LoopStepBar.test.jsx — Iter 294 (Frontend Layer 1, Batch 1
 * pattern-establishing prototype)
 *
 * Charter: State-Sync Behavior Testing (RTL). Every assertion here
 * queries the RENDERED DOM via `screen.getByTestId` — never a prop
 * or internal state. This is the exact pattern the Frontend QA
 * Charter mandates and the failure class that iter288's bug batch
 * belonged to (stepper stayed orange after FAIL because the UI
 * treated "prop present" as "state correct" without observing what
 * was actually rendered).
 *
 * Three tests, mirroring the 3-test template in the charter:
 *   1. reaches-correct-terminal-state: executing → failed paints
 *      the executing step red (data-step-state="error"), NOT orange.
 *   2. clears-stale-prior-state: same component instance re-renders
 *      with phase="error"; the previously-orange EXECUTE step must
 *      flip to error, and no step may remain in "active" state.
 *   3. race-condition: given phase="error", even if the caller has
 *      set errorStep to a valid step id, no step may render with
 *      data-step-state="active" — a late "executing" frame arriving
 *      after terminal must never re-flip the color. (LoopStepBar
 *      itself is pure; this test asserts the invariant it must
 *      satisfy so ChatPanel's loopTerminalRef guard has something
 *      to guarantee.)
 *
 * If any of the three assertions passes with a broken LoopStepBar
 * (e.g. wrong data-step-state), that's a signal the test is
 * tautological — treat like the mutation-testing findings in iter289.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import LoopStepBar from "../LoopStepBar.jsx";


describe("LoopStepBar — state-sync behavior (iter294)", () => {
  it("reaches-correct-terminal-state: executing → failed paints EXECUTE red, not orange", () => {
    // Render the executing state first — sanity — EXECUTE is orange.
    const { rerender } = render(<LoopStepBar phase="executing" errorStep={0} />);
    const executeStep = screen.getByTestId("loop-step-execute");
    expect(executeStep.getAttribute("data-step-state")).toBe("active");

    // Now the terminal FAIL arrives with the failing phase = execute
    // (errorStep=2). The step must flip to "error".
    rerender(<LoopStepBar phase="error" errorStep={2} />);
    const executeStepAfter = screen.getByTestId("loop-step-execute");
    expect(executeStepAfter.getAttribute("data-step-state")).toBe("error");
    // And it MUST NOT still be "active" (this is the exact bug from
    // iter288 — a false pass here would prove the fix isn't real).
    expect(executeStepAfter.getAttribute("data-step-state")).not.toBe("active");
  });

  it("clears-stale-prior-state: no step remains 'active' once phase becomes error", () => {
    const { rerender } = render(<LoopStepBar phase="executing" errorStep={0} />);
    // Sanity — exactly one step is 'active' during executing.
    const stepsBefore = ["plan", "execute", "verify", "security", "ship"]
      .map(k => screen.getByTestId(`loop-step-${k}`).getAttribute("data-step-state"));
    expect(stepsBefore.filter(s => s === "active")).toHaveLength(1);

    // Transition to a ship-time failure (errorStep=5). Now:
    //   • no step may be 'active',
    //   • SHIP must be 'error',
    //   • EXECUTE must NOT be red (this catches the pre-iter288
    //     bug where errorStep was hard-coded to 2).
    rerender(<LoopStepBar phase="error" errorStep={5} />);
    const stepsAfter = ["plan", "execute", "verify", "security", "ship"]
      .map(k => screen.getByTestId(`loop-step-${k}`).getAttribute("data-step-state"));
    expect(stepsAfter.filter(s => s === "active")).toHaveLength(0);
    expect(screen.getByTestId("loop-step-ship").getAttribute("data-step-state"))
      .toBe("error");
    expect(screen.getByTestId("loop-step-execute").getAttribute("data-step-state"))
      .not.toBe("error");
  });

  it("race-condition: phase=error blocks any step from rendering as 'active', regardless of errorStep target", () => {
    // Simulate the race: caller's state briefly held phase="error"
    // and errorStep=2 (execute failed). If a late executing prop
    // had been fed AFTER, ChatPanel's loopTerminalRef would drop it —
    // but even if it didn't, LoopStepBar itself must never paint a
    // step as 'active' when phase === "error". That's the invariant.
    render(<LoopStepBar phase="error" errorStep={2} />);
    const kinds = ["plan", "execute", "verify", "security", "ship"]
      .map(k => screen.getByTestId(`loop-step-${k}`).getAttribute("data-step-state"));
    expect(kinds.some(k => k === "active")).toBe(false);
    // And errorStep=2 (EXECUTE) must be the ONLY one carrying error.
    expect(kinds.filter(k => k === "error")).toHaveLength(1);
    expect(screen.getByTestId("loop-step-execute").getAttribute("data-step-state"))
      .toBe("error");
  });
});
