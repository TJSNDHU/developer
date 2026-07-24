/**
 * PlanApprovalCard.test.jsx — Iter 296 (Frontend Layer 1, Batch 2)
 *
 * State-sync behavior tests — same 3-test template as
 * LoopStepBar.test.jsx (iter294) / AgentStatusBar.test.jsx (iter295).
 *
 * PlanApprovalCard is the inline gate that shows at the end of Loop
 * Step 1. Its safety-critical invariants:
 *   1. When enabled, Approve fires onApprove exactly once.
 *   2. When disabled (a request is already in-flight), clicking
 *      MUST NOT re-fire the handler — this is the double-execute
 *      bug class the founder called out (iter288).
 *   3. Cancel is wired ONLY to onCancel — no cross-wiring to
 *      onApprove that could silently start the loop when the user
 *      meant to abort.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import PlanApprovalCard from "../PlanApprovalCard.jsx";


describe("PlanApprovalCard — state-sync behavior (iter296)", () => {
  it("reaches-correct-terminal-state: enabled Approve fires onApprove exactly once and does not fire onCancel", () => {
    const onApprove = vi.fn();
    const onCancel  = vi.fn();
    render(
      <PlanApprovalCard onApprove={onApprove} onCancel={onCancel}
                         disabled={false} />
    );
    // Card is on screen with both buttons.
    expect(screen.getByTestId("plan-approval-card")).toBeInTheDocument();
    const approve = screen.getByTestId("plan-approve-btn");
    expect(approve).not.toBeDisabled();

    // User clicks Approve. The handler fires exactly once — no
    // accidental double invocation from a hover animation handler
    // re-triggering, no cross-wire into onCancel.
    fireEvent.click(approve);
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("clears-stale-prior-state: disabled=true blocks the click even after a same-instance rerender from an enabled state", () => {
    const onApprove = vi.fn();
    const onCancel  = vi.fn();
    const { rerender } = render(
      <PlanApprovalCard onApprove={onApprove} onCancel={onCancel}
                         disabled={false} />
    );
    // Sanity — enabled Approve works.
    fireEvent.click(screen.getByTestId("plan-approve-btn"));
    expect(onApprove).toHaveBeenCalledTimes(1);

    // Parent flips disabled=true (a submit is now in-flight) inside
    // the SAME instance. The button must reflect disabled=true in
    // the SAME render AND any subsequent click must NOT re-fire
    // onApprove — the exact double-execute race iter288 was about.
    rerender(
      <PlanApprovalCard onApprove={onApprove} onCancel={onCancel}
                         disabled={true} />
    );
    const approveNow = screen.getByTestId("plan-approve-btn");
    expect(approveNow).toBeDisabled();
    fireEvent.click(approveNow);
    // Still 1 — the disabled attr suppressed the click. If a future
    // refactor stopped honouring the disabled prop on the button,
    // this test would flip to 2 and fail.
    expect(onApprove).toHaveBeenCalledTimes(1);

    // Cancel must ALSO be disabled in the same render — asserting
    // per-button so a partial-disable regression fails loudly.
    const cancelNow = screen.getByTestId("plan-cancel-btn");
    expect(cancelNow).toBeDisabled();
    fireEvent.click(cancelNow);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("race-condition: Cancel is wired exclusively to onCancel — never triggers onApprove even in a rapid-click sequence", () => {
    // The invariant we're locking: cross-wiring the two handlers
    // (e.g. a refactor that moves onApprove/onCancel into a single
    // dispatcher and gets the branch wrong) would allow a
    // Cancel-click to start the loop — a silent data-loss class.
    const onApprove = vi.fn();
    const onCancel  = vi.fn();
    render(
      <PlanApprovalCard onApprove={onApprove} onCancel={onCancel}
                         disabled={false} />
    );
    const cancel = screen.getByTestId("plan-cancel-btn");
    // Fire Cancel three times in a row — simulating a jittery
    // trackpad click. Every event lands on onCancel; none of them
    // ever crosses over to onApprove.
    fireEvent.click(cancel);
    fireEvent.click(cancel);
    fireEvent.click(cancel);
    expect(onCancel).toHaveBeenCalledTimes(3);
    expect(onApprove).not.toHaveBeenCalled();
  });
});
