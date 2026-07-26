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

describe("Iter 309 · Item C refined LoopLiveFeed empty-state placeholder", () => {
  // Iter 309 · Item C — founder approved a simplified single-line
  // phase-aware placeholder ("~ Opening {phase} stream…") instead of
  // the prior 24-line branching switch. These expectations track the
  // new behavior. The narration event stream typically lands within
  // 1-2s of loop start, so this text is barely visible in practice.
  const placeholderExpectations = [
    ["executing", /Opening executing stream/i],
    ["verifying", /Opening verifying stream/i],
    ["scanning", /Opening scanning stream/i],
    ["shipping", /Opening shipping stream/i],
    ["self_healing", /Opening self_healing stream/i],
    ["paused_for_user", /Paused/i],
    ["planning", /Opening planning stream/i],
    ["", /Opening event stream/i],
  ];

  it.each(placeholderExpectations)("phase=%s shows the correct placeholder", (phase, expectedText) => {
    render(<LoopLiveFeed loopId="loop_643" phase={phase} terminal={false} />);
    expect(screen.getByTestId("loop-live-feed-placeholder")).toHaveTextContent(expectedText);
    cleanup();
  });
});