import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import LoopStepBar from "../LoopStepBar.jsx";
import LoopLiveFeed from "../LoopLiveFeed.jsx";

describe("Iter 308 LoopStepBar backend phase mapping", () => {
  const activeExpectations = [
    ["planning", "loop-step-plan"],
    ["awaiting_confirmation", "loop-step-plan"],
    ["executing", "loop-step-execute"],
    ["self_healing", "loop-step-execute"],
    ["paused_for_user", "loop-step-execute"],
    ["verifying", "loop-step-verify"],
    ["scanning", "loop-step-security"],
    ["shipping", "loop-step-ship"],
  ];

  it.each(activeExpectations)("renders %s on the expected active step", (phase, testId) => {
    render(<LoopStepBar phase={phase} />);
    expect(screen.getByTestId(testId)).toHaveAttribute("data-step-state", "active");
    cleanup();
  });

  it("renders completed as all five steps done", () => {
    render(<LoopStepBar phase="completed" />);
    for (const testId of [
      "loop-step-plan",
      "loop-step-execute",
      "loop-step-verify",
      "loop-step-security",
      "loop-step-ship",
    ]) {
      expect(screen.getByTestId(testId)).toHaveAttribute("data-step-state", "done");
    }
  });

  it.each(["failed", "aborted", "expired"])(
    "renders terminal error phase %s on the supplied error step",
    (phase) => {
      render(<LoopStepBar phase={phase} errorStep={2} />);
      expect(screen.getByTestId("loop-step-execute")).toHaveAttribute("data-step-state", "error");
      cleanup();
    },
  );
});

describe("Iter 308 LoopLiveFeed dynamic empty-state placeholder", () => {
  const placeholderExpectations = [
    ["executing", /Executing|generating file content/i],
    ["verifying", /Verifying/i],
    ["scanning", /Vanguard scan/i],
    ["shipping", /Shipping|committing/i],
    ["self_healing", /Self-healing/i],
    ["paused_for_user", /Paused/i],
    ["planning", /Waiting for plan approval/i],
    ["", /Waiting for plan approval/i],
  ];

  it.each(placeholderExpectations)("phase=%s shows the correct placeholder", (phase, expectedText) => {
    render(<LoopLiveFeed loopId="loop_643" phase={phase} terminal={false} />);
    expect(screen.getByTestId("loop-live-feed-placeholder")).toHaveTextContent(expectedText);
    cleanup();
  });
});