/**
 * Iter 331 — mid-run monotonic invariant ("EXECUTE + SHIP amber
 * together" founder bug, prod ship gate screenshot).
 *
 * The engine is strictly sequential, so at most ONE step may spin at
 * a time. A stale "pending" tone below the pipeline frontier must
 * resolve green in BOTH surfaces:
 *   - LoopStepBar chips (Rule 0-c)
 *   - LoopLiveFeed folded narration lines (resolveStalePendingByFrontier)
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import LoopStepBar from "../LoopStepBar.jsx";
import { resolveStalePendingByFrontier } from "../LoopLiveFeed.jsx";

const state = (k) =>
  screen.getByTestId(`loop-step-${k}`).getAttribute("data-step-state");

describe("Iter 331 · Rule 0-c — single-active frontier (LoopStepBar)", () => {
  it("founder repro: stale execute pending at ship gate → EXECUTE green, only SHIP spins", () => {
    render(<LoopStepBar
      phase="paused_for_user"
      stepTones={{ plan: "success", execute: "pending",
                   verify: "success", scan: "success", ship: "pending" }}
    />);
    expect(state("plan")).toBe("done");
    expect(state("execute")).toBe("done");     // stale spinner resolved
    expect(state("verify")).toBe("done");
    expect(state("security")).toBe("done");
    expect(state("ship")).toBe("active");      // the ONLY active step
  });

  it("genuine mid-execute pending is NOT falsely resolved", () => {
    render(<LoopStepBar phase="executing" stepTones={{ execute: "pending" }} />);
    expect(state("execute")).toBe("active");
    expect(state("verify")).toBe("future");
  });

  it("stale execute pending during verify → EXECUTE green, VERIFY spins", () => {
    render(<LoopStepBar
      phase="verifying"
      stepTones={{ execute: "pending", verify: "pending" }}
    />);
    expect(state("execute")).toBe("done");
    expect(state("verify")).toBe("active");
  });

  it("danger tones are untouched by the frontier rule", () => {
    render(<LoopStepBar
      phase="verifying"
      stepTones={{ execute: "danger", verify: "pending" }}
    />);
    expect(state("execute")).toBe("error");
    expect(state("verify")).toBe("active");
  });
});

describe("Iter 331 · resolveStalePendingByFrontier (LoopLiveFeed)", () => {
  const line = (step, tone, text) => ({ key: `${step}:${text}`, step, tone, text, tsEpoch: 1 });

  it("resolves earlier pending lines once a later step narrates", () => {
    const out = resolveStalePendingByFrontier([
      line("execute", "pending", "Writing tests/test_smoke.py"),
      line("verify", "success", "Verify clean"),
      line("ship", "pending", "Resolving GitHub credentials"),
    ]);
    expect(out[0].tone).toBe("success");
    expect(out[0].__resolvedByFrontier).toBe(true);
    expect(out[2].tone).toBe("pending");   // frontier line keeps spinning
  });

  it("no false resolution while only one step is narrating", () => {
    const out = resolveStalePendingByFrontier([
      line("execute", "pending", "Writing a.py"),
      line("execute", "pending", "Writing b.py"),
    ]);
    expect(out.every((l) => l.tone === "pending")).toBe(true);
  });

  it("non-narration/unknown steps pass through untouched", () => {
    const out = resolveStalePendingByFrontier([
      line("", "pending", "misc"),
      line("ship", "pending", "shipping"),
    ]);
    expect(out[0].tone).toBe("pending");
  });
});
