/**
 * LoopActionCards.iter332_ship_gate.test.jsx — Iter 332 P0 fix.
 *
 * Founder prod smoke test: the ship human-review gate rendered the
 * generic retry/skip/abort card with NO "Approve & Ship" button, and
 * "Skip this step" put the engine into an infinite Execute cycle.
 * These tests pin the new dedicated gate UI.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UserActionCard } from "../LoopActionCards.jsx";

describe("UserActionCard — ship_human_review gate (Iter 332)", () => {
  it("renders the dedicated ship-review card, not the generic one", () => {
    render(
      <UserActionCard
        phase="ship"
        gateType="ship_human_review"
        message="Test/fixture files were modified — human review required."
        testsTouched={["tests/test_a.py"]}
        onAction={() => {}}
      />
    );
    expect(screen.getByTestId("ship-review-gate-card")).toBeInTheDocument();
    expect(screen.queryByTestId("user-action-card")).toBeNull();
  });

  it("shows Approve & Ship + Cancel ship; hides retry/skip/abort", () => {
    render(
      <UserActionCard
        phase="ship"
        gateType="ship_human_review"
        message="review needed"
        onAction={() => {}}
      />
    );
    expect(screen.getByTestId("loop-approve-ship-btn")).toBeInTheDocument();
    expect(screen.getByTestId("loop-cancel-ship-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("loop-skip-btn")).toBeNull();
    expect(screen.queryByTestId("loop-retry-btn")).toBeNull();
    expect(screen.queryByTestId("loop-abort-btn")).toBeNull();
  });

  it("Approve & Ship fires onAction('approve_ship')", () => {
    const onAction = vi.fn();
    render(
      <UserActionCard phase="ship" gateType="ship_human_review"
                      message="m" onAction={onAction} />
    );
    fireEvent.click(screen.getByTestId("loop-approve-ship-btn"));
    expect(onAction).toHaveBeenCalledWith("approve_ship");
  });

  it("Cancel ship fires onAction('cancel_ship')", () => {
    const onAction = vi.fn();
    render(
      <UserActionCard phase="ship" gateType="ship_human_review"
                      message="m" onAction={onAction} />
    );
    fireEvent.click(screen.getByTestId("loop-cancel-ship-btn"));
    expect(onAction).toHaveBeenCalledWith("cancel_ship");
  });

  it("lists touched test files", () => {
    render(
      <UserActionCard phase="ship" gateType="ship_human_review"
                      message="m"
                      testsTouched={["tests/a.test.js", "tests/b.test.js"]}
                      onAction={() => {}} />
    );
    const pre = screen.getByTestId("ship-review-tests-touched");
    expect(pre.textContent).toContain("tests/a.test.js");
    expect(pre.textContent).toContain("tests/b.test.js");
  });

  it("buttons disabled while busy", () => {
    render(
      <UserActionCard phase="ship" gateType="ship_human_review"
                      message="m" busy onAction={() => {}} />
    );
    expect(screen.getByTestId("loop-approve-ship-btn")).toBeDisabled();
    expect(screen.getByTestId("loop-cancel-ship-btn")).toBeDisabled();
  });

  it("generic card unchanged when no gateType", () => {
    render(
      <UserActionCard phase="verify" message="paused"
                      errors={["boom"]} onAction={() => {}} />
    );
    expect(screen.getByTestId("user-action-card")).toBeInTheDocument();
    expect(screen.getByTestId("loop-skip-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("loop-approve-ship-btn")).toBeNull();
  });
});
