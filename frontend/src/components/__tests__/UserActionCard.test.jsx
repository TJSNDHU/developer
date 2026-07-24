/**
 * UserActionCard.test.jsx — Iter 302 (Frontend QA Charter Layer 1 audit)
 *
 * State-sync behavior tests for the loop's paused-for-user card.
 * Same 3-test template.
 *
 * UserActionCard renders when a loop pauses and demands explicit
 * input (`requires_user_action=true` from the SSE event). Founder
 * concern from the bug list: "PlanApprovalCard rendering for a
 * failed loop" — a related bug class this test file locks down for
 * UserActionCard's variant: retry/skip/abort dispatch cross-wiring.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UserActionCard } from "../LoopActionCards.jsx";


describe("UserActionCard — state-sync behavior (iter302)", () => {
  it("reaches-correct-terminal-state: renders phase + message + errors block correctly", () => {
    render(
      <UserActionCard
        phase="verify"
        message="Lint failed on 2 files; retry?"
        errors={["ruff:E501 line too long", "eslint no-unused-vars"]}
        onAction={vi.fn()}
        busy={false}
      />
    );
    const card = screen.getByTestId("user-action-card");
    expect(card.getAttribute("data-phase")).toBe("verify");
    // aria-label is the anchor screen readers announce; must include
    // the "paused" phrasing so the user's tech isn't left guessing.
    expect(card.getAttribute("aria-label"))
      .toMatch(/loop paused/i);
    // Phase code is rendered inline.
    expect(card.textContent).toContain("verify");
    expect(card.textContent).toContain("Lint failed on 2 files");
    // Errors block renders the flattened list.
    const errBlock = screen.getByTestId("user-action-errors");
    expect(errBlock.textContent).toContain("ruff:E501");
    expect(errBlock.textContent).toContain("eslint no-unused-vars");
  });

  it("clears-stale-prior-state: rerender with new phase + no errors clears the error block", () => {
    const { rerender } = render(
      <UserActionCard
        phase="verify" message="verify failed"
        errors={["ruff:E501"]} onAction={vi.fn()} busy={false}
      />
    );
    expect(screen.getByTestId("user-action-errors")).toBeInTheDocument();

    // Engine moved to scan phase with a clean message — the STALE
    // ruff error must NOT survive into the new render.
    rerender(
      <UserActionCard
        phase="scan" message="scan detected a secret"
        errors={undefined} onAction={vi.fn()} busy={false}
      />
    );
    const card = screen.getByTestId("user-action-card");
    expect(card.getAttribute("data-phase")).toBe("scan");
    expect(card.textContent).toContain("scan detected a secret");
    // Error block MUST unmount when errors becomes undefined —
    // otherwise stale error text from verify lingers into scan.
    expect(screen.queryByTestId("user-action-errors")).toBeNull();
    expect(screen.queryByText(/ruff:E501/)).toBeNull();
  });

  it("race-condition: each button wires exclusively to its action — no cross-wiring", () => {
    const onAction = vi.fn();
    render(
      <UserActionCard
        phase="verify" message="paused" errors={[]}
        onAction={onAction} busy={false}
      />
    );
    // Retry click fires ONLY onAction("retry", ...) — never "abort"/"skip".
    fireEvent.click(screen.getByTestId("loop-retry-btn"));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction.mock.calls[0][0]).toBe("retry");

    fireEvent.click(screen.getByTestId("loop-skip-btn"));
    expect(onAction).toHaveBeenCalledTimes(2);
    expect(onAction.mock.calls[1][0]).toBe("skip");

    fireEvent.click(screen.getByTestId("loop-abort-btn"));
    expect(onAction).toHaveBeenCalledTimes(3);
    expect(onAction.mock.calls[2][0]).toBe("abort");

    // busy=true disables all three — no click may fire onAction.
    // Same "in-flight double-execute" bug class as PlanApprovalCard
    // (iter296 test 2).
    const { rerender } = render(
      <UserActionCard
        phase="verify" message="paused" errors={[]}
        onAction={onAction} busy={true}
      />
    );
    fireEvent.click(screen.getAllByTestId("loop-retry-btn")[1]);
    fireEvent.click(screen.getAllByTestId("loop-abort-btn")[1]);
    // Call count unchanged — busy suppressed both clicks.
    expect(onAction).toHaveBeenCalledTimes(3);
  });
});
