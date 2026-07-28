/**
 * LoopStepBar.iter331_raw_phase_alias.test.jsx — Iter 331
 *
 * Bug 1 lock — raw engine phases leaking into `loopPhase` (ChatPanel
 * timeout-recovery sets `active.phase` verbatim; ship-gate hydration
 * sets the literal "ship") previously mapped to step 0 in
 * PHASE_TO_STEP, so PLAN rendered gray "future" during a live
 * EXECUTE. The aliases plan/execute/verify/scan/ship/self_heal now
 * map 1:1 to their steps.
 *
 * Bug 2 lock — the active ECG strip must carry the ecg-scroll
 * animation + its keyframes must be present in the component output
 * (verified live in the preview browser: computed transform samples
 * -43.7 → -5.3 → -24.5 over 700ms, animationPlayState=running).
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import LoopStepBar from "../LoopStepBar.jsx";

const stepState = (key) =>
  screen.getByTestId(`loop-step-${key}`).getAttribute("data-step-state");

describe("Iter 331 · Bug 1 — raw engine phase aliases (PLAN must never be gray mid-loop)", () => {
  it("phase='execute' (raw DB phase) → PLAN done, EXECUTE active", () => {
    render(<LoopStepBar phase="execute" stepTones={{}} />);
    expect(stepState("plan")).toBe("done");
    expect(stepState("execute")).toBe("active");
    expect(stepState("verify")).toBe("future");
  });

  it("phase='verify' → PLAN + EXECUTE done, VERIFY active", () => {
    render(<LoopStepBar phase="verify" stepTones={{}} />);
    expect(stepState("plan")).toBe("done");
    expect(stepState("execute")).toBe("done");
    expect(stepState("verify")).toBe("active");
  });

  it("phase='scan' → steps 1-3 done, SCAN active", () => {
    render(<LoopStepBar phase="scan" stepTones={{}} />);
    expect(stepState("plan")).toBe("done");
    expect(stepState("verify")).toBe("done");
    expect(stepState("security")).toBe("active");
  });

  it("phase='ship' (ship-gate hydration literal) → steps 1-4 done, SHIP active", () => {
    render(<LoopStepBar phase="ship" stepTones={{}} />);
    expect(stepState("plan")).toBe("done");
    expect(stepState("execute")).toBe("done");
    expect(stepState("verify")).toBe("done");
    expect(stepState("security")).toBe("done");
    expect(stepState("ship")).toBe("active");
  });

  it("phase='self_heal' → PLAN done, EXECUTE active", () => {
    render(<LoopStepBar phase="self_heal" stepTones={{}} />);
    expect(stepState("plan")).toBe("done");
    expect(stepState("execute")).toBe("active");
  });

  it("stepTones.plan='success' (Iter 331 backend plan narration) → PLAN done in any phase", () => {
    render(<LoopStepBar phase="plan_pending" stepTones={{ plan: "success" }} />);
    expect(stepState("plan")).toBe("done");
  });
});

describe("Iter 331 · Bug 2 — active ECG animation lock", () => {
  it("active ECG strip carries ecg-scroll animation and keyframes exist", () => {
    const { container } = render(<LoopStepBar phase="execute" stepTones={{}} />);
    const strip = screen.getByTestId("loop-step-ecg-execute");
    expect(strip.getAttribute("data-variant")).toBe("active");
    const g = strip.querySelector("svg g");
    expect(g).not.toBeNull();
    expect(g.style.animation).toContain("ecg-scroll");
    expect(container.innerHTML).toContain("@keyframes ecg-scroll");
  });

  it("resolved steps render flat (no animated <g>)", () => {
    render(<LoopStepBar phase="execute" stepTones={{}} />);
    const plan = screen.getByTestId("loop-step-ecg-plan");
    expect(plan.getAttribute("data-variant")).toBe("success");
    expect(plan.querySelector("svg g")).toBeNull();
  });
});
