/**
 * LoopProgressBubble.test.jsx — Iter 331 · collapsible loop transcript.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import LoopProgressBubble, { isLoopProgressContent } from "../LoopProgressBubble.jsx";

const LOOP_TEXT = [
  "Plan ready — awaiting approval.",
  "**Step 2 / 5 — Execute**  Plan approved — execution started.",
  "**Step 2 / 5 — Execute**  Executing — 2 file(s) planned…",
  "**Step 2 / 5 — Execute**  Writing tests/test_smoke.py",
  "**Aborted**  Loop cancelled by user.",
].join("\n");

describe("Iter 331 · isLoopProgressContent detector", () => {
  it("matches loop step transcripts (live + persisted history)", () => {
    expect(isLoopProgressContent(LOOP_TEXT)).toBe(true);
    expect(isLoopProgressContent("Plan ready — awaiting approval.\n**Aborted**  x")).toBe(true);
  });
  it("never matches normal prose replies", () => {
    expect(isLoopProgressContent("Here is how JWT auth works, **step** by step.")).toBe(false);
    expect(isLoopProgressContent("")).toBe(false);
  });
});

describe("Iter 331 · LoopProgressBubble", () => {
  it("terminal transcript collapses by default with count + Aborted status", () => {
    render(
      <LoopProgressBubble text={LOOP_TEXT} streaming={false}>
        <div data-testid="full-body">FULL</div>
      </LoopProgressBubble>,
    );
    expect(screen.getByTestId("loop-progress-bubble").getAttribute("data-expanded")).toBe("false");
    expect(screen.queryByTestId("full-body")).toBeNull();
    expect(screen.getByTestId("loop-progress-count")).toHaveTextContent("3 step events");
    expect(screen.getByTestId("loop-progress-status")).toHaveTextContent("Aborted");
  });

  it("click expands, second click collapses", () => {
    render(
      <LoopProgressBubble text={LOOP_TEXT} streaming={false}>
        <div data-testid="full-body">FULL</div>
      </LoopProgressBubble>,
    );
    fireEvent.click(screen.getByTestId("loop-progress-toggle"));
    expect(screen.getByTestId("full-body")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("loop-progress-toggle"));
    expect(screen.queryByTestId("full-body")).toBeNull();
  });

  it("live run (streaming) stays expanded with Running status", () => {
    const live = "**Step 2 / 5 — Execute**  Writing a.py";
    render(
      <LoopProgressBubble text={live} streaming>
        <div data-testid="full-body">FULL</div>
      </LoopProgressBubble>,
    );
    expect(screen.getByTestId("loop-progress-bubble").getAttribute("data-expanded")).toBe("true");
    expect(screen.getByTestId("full-body")).toBeInTheDocument();
    expect(screen.getByTestId("loop-progress-status")).toHaveTextContent("Running…");
  });

  it("failed + shipped statuses detected", () => {
    const { rerender } = render(
      <LoopProgressBubble text={"**Step 3 / 5 — Verify**  x\n**Failed**  boom"} streaming={false} />,
    );
    expect(screen.getByTestId("loop-progress-status")).toHaveTextContent("Failed");
    rerender(
      <LoopProgressBubble text={"**Step 5 / 5 — Ship**  Ship complete ✅"} streaming={false} />,
    );
    expect(screen.getByTestId("loop-progress-status")).toHaveTextContent("Shipped");
  });
});
