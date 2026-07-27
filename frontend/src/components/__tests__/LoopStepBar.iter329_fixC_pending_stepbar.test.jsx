/**
 * LoopStepBar.iter329_fixC_pending_stepbar.test.jsx
 *
 * Iter 329 · Fix C · Bug 2 — LoopStepBar EXECUTE stuck spinning after
 * loop reached terminal-success (real founder-observed bug on ship
 * commit 0b79db0).
 *
 * Founder screenshot: PLAN ✓, EXECUTE (amber, still spinning), VERIFY
 * ✓, SCAN ✓, SHIP ✓. Logically impossible — SHIP can't be done if
 * EXECUTE isn't. Root cause: `stepTones.execute` stayed at "pending"
 * because the backend emitted an EXECUTE narration but never a
 * correlation_id-matching resolver frame (same class as LoopLiveFeed
 * Fix B). ecgVariant() Rule 0's terminal-success override only
 * covered step 5 (SHIP), not the other 4 phases.
 *
 * Fix: extend Rule 0 to resolve ANY pending stepTone on terminal-
 * success (isDone → success) or terminal-failure (isError with a
 * pending tone → warning; only the actual error step is danger).
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import LoopStepBar from "../LoopStepBar.jsx";


describe("Iter 329 · Fix C · Bug 2 — LoopStepBar terminal pending resolver", () => {
  it("SHIP-SUCCESS (isDone) with pending EXECUTE tone → EXECUTE renders as done, NOT active (founder-observed bug)", () => {
    // Reproduce the exact founder-observed shape: chat shows ship
    // completed, phase is "completed" (or "done"/"shipped"), but the
    // EXECUTE narration never got its resolver frame so its
    // stepTones entry is still "pending". VERIFY/SCAN/SHIP already
    // have "success" tones.
    render(
      <LoopStepBar
        phase="completed"
        stepTones={{
          plan:    "success",
          execute: "pending",   // ← the bug shape
          verify:  "success",
          scan:    "success",
          ship:    "success",
        }}
      />
    );
    const exec = screen.getByTestId("loop-step-execute");
    expect(exec.getAttribute("data-step-state")).toBe("done");
    // ECG under EXECUTE must be flatlined green (success), not
    // scrolling amber (active).
    const ecg = screen.getByTestId("loop-step-ecg-execute");
    expect(ecg.getAttribute("data-variant")).toBe("success");
  });

  it("SHIP-SUCCESS with all four pending tones → every step resolves to done", () => {
    // Extreme case: backend emitted zero resolver frames for any
    // step. Loop reached completed. All should still render done.
    render(
      <LoopStepBar
        phase="done"
        stepTones={{
          plan:    "pending",
          execute: "pending",
          verify:  "pending",
          scan:    "pending",   // narrationKey for step id=4
          ship:    "pending",
        }}
      />
    );
    // Note: step id=4's testid uses `key="security"` (its narrationKey
    // is "scan"). All other steps use their key as testid.
    const stepKeys = ["plan", "execute", "verify", "security", "ship"];
    for (const key of stepKeys) {
      expect(screen.getByTestId(`loop-step-${key}`)
        .getAttribute("data-step-state")).toBe("done");
      expect(screen.getByTestId(`loop-step-ecg-${key}`)
        .getAttribute("data-variant")).toBe("success");
    }
  });

  it("FAILED at EXECUTE → EXECUTE is danger, later pending steps are future (not active)", () => {
    render(
      <LoopStepBar
        phase="failed"
        errorStep={2}
        stepTones={{
          plan:    "success",
          execute: "pending",   // died mid-execute, no resolver frame
        }}
      />
    );
    const exec = screen.getByTestId("loop-step-execute");
    expect(exec.getAttribute("data-step-state")).toBe("error");
    // Steps 3-5 should not be spinning/active on a failed loop.
    for (const key of ["verify", "security", "ship"]) {
      expect(screen.getByTestId(`loop-step-${key}`)
        .getAttribute("data-step-state")).not.toBe("active");
    }
  });

  it("REGRESSION — running (non-terminal) executing phase: pending EXECUTE still renders active", () => {
    // The fix must NOT flip pending tones on a running loop.
    render(
      <LoopStepBar
        phase="executing"
        stepTones={{
          plan:    "success",
          execute: "pending",
        }}
      />
    );
    const exec = screen.getByTestId("loop-step-execute");
    expect(exec.getAttribute("data-step-state")).toBe("active");
    expect(screen.getByTestId("loop-step-ecg-execute")
      .getAttribute("data-variant")).toBe("active");
  });

  it("REGRESSION — shipped phase (Rule 0 legacy) still forces SHIP green when no ship tone present", () => {
    render(
      <LoopStepBar
        phase="shipped"
        stepTones={{ plan: "success", execute: "success", verify: "success", scan: "success" }}
      />
    );
    expect(screen.getByTestId("loop-step-ship")
      .getAttribute("data-step-state")).toBe("done");
  });
});
