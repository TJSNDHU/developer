import React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import LoopStepBar from "../LoopStepBar.jsx";

vi.mock("../../lib/loopApi", () => ({
  getActiveLoop: vi.fn(),
  cancelLoop: vi.fn(),
}));

import { getActiveLoop } from "../../lib/loopApi";
import LoopStatusChip from "../LoopStatusChip.jsx";

async function flushPromises() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function advance(ms) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("Iter 323 Bug A — LoopStepBar SHIP terminal-success state", () => {
  afterEach(() => cleanup());

  test("phase=completed forces SHIP done/green even when stepTones.ship is stale pending", () => {
    render(<LoopStepBar phase="completed" stepTones={{ ship: "pending" }} />);
    const ship = screen.getByTestId("loop-step-ship");
    expect(ship).toHaveAttribute("data-step-state", "done");
    expect(window.getComputedStyle(ship).color.replace(/\s/g, "")).toBe("rgb(34,197,94)");
    expect(screen.getByTestId("loop-step-ecg-ship")).toHaveAttribute("data-variant", "success");
  });

  test("phase=shipping still shows SHIP orange/active while ship is actually running", () => {
    render(<LoopStepBar phase="shipping" stepTones={{ ship: "pending" }} />);
    const ship = screen.getByTestId("loop-step-ship");
    expect(ship).toHaveAttribute("data-step-state", "active");
    expect(window.getComputedStyle(ship).color.replace(/\s/g, "")).toBe("rgb(255,102,8)");
    expect(screen.getByTestId("loop-step-ecg-ship")).toHaveAttribute("data-variant", "active");
  });
});

describe("Iter 323 Bug B — LoopStatusChip terminal grace behavior", () => {
  const liveShipLoop = {
    loop_id: "loop_7d4f8ee67cfd44",
    state: "paused_for_user",
    phase: "ship",
    project_id: "proj_aurem",
  };
  const freshLoop = {
    loop_id: "loop_fresh12345678",
    state: "executing",
    phase: "execute",
    project_id: "proj_aurem",
  };

  beforeEach(() => {
    vi.useFakeTimers();
    getActiveLoop.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  test("active→null keeps SHIPPED chip mounted (Fix B: no auto-unmount on success — persists until Done click)", async () => {
    getActiveLoop
      .mockResolvedValueOnce({ ok: true, active: liveShipLoop })
      .mockResolvedValue({ ok: true, active: null });

    render(<LoopStatusChip projectId="proj_aurem" />);
    await flushPromises();

    const liveChip = screen.getByTestId("loop-status-chip");
    expect(liveChip).toHaveAttribute("data-terminal", "false");
    expect(screen.getByTestId("loop-status-chip-stop")).toBeInTheDocument();

    await advance(10_000);

    const terminalChip = screen.getByTestId("loop-status-chip");
    expect(terminalChip).toHaveAttribute("data-terminal", "true");
    expect(screen.getByTestId("loop-status-chip-phase")).toHaveTextContent("LOOP · SHIPPED");
    expect(screen.getByTestId("loop-status-chip-id")).toHaveTextContent("id · e67cfd44");
    expect(screen.queryByTestId("loop-status-chip-stop")).toBeNull();

    // Iter 329 · Task 2 · Fix B — terminal-SUCCESS persists past the
    // former 30s grace. Chip only unmounts when user clicks Done or
    // a new loop starts. Previous test asserted auto-unmount at 30s;
    // that's now expected behaviour ONLY for terminal-failure.
    await advance(60_000);
    expect(screen.getByTestId("loop-status-chip")).toBeInTheDocument();
    expect(screen.getByTestId("loop-status-chip-done")).toBeInTheDocument();

    // Clicking Done unmounts the chip immediately (Task 2 inline UX).
    await act(async () => { screen.getByTestId("loop-status-chip-done").click(); });
    await flushPromises();
    expect(screen.queryByTestId("loop-status-chip")).toBeNull();
  });

  test("fresh loop during terminal grace clears stale snapshot and shows live loop", async () => {
    getActiveLoop
      .mockResolvedValueOnce({ ok: true, active: liveShipLoop })
      .mockResolvedValueOnce({ ok: true, active: null })
      // Setting terminalSnapshot intentionally recreates poll() in current
      // code and triggers one immediate grace-window poll; keep that one
      // null so the next 10s interval represents the fresh loop starting
      // during the visible grace period.
      .mockResolvedValueOnce({ ok: true, active: null })
      .mockResolvedValue({ ok: true, active: freshLoop });

    render(<LoopStatusChip projectId="proj_aurem" />);
    await flushPromises();

    await advance(10_000);
    expect(screen.getByTestId("loop-status-chip")).toHaveAttribute("data-terminal", "true");
    expect(screen.getByTestId("loop-status-chip-phase")).toHaveTextContent("LOOP · SHIPPED");

    await advance(10_000);
    const chip = screen.getByTestId("loop-status-chip");
    expect(chip).toHaveAttribute("data-terminal", "false");
    expect(screen.getByTestId("loop-status-chip-phase")).toHaveTextContent("LOOP · EXECUTING");
    expect(screen.getByTestId("loop-status-chip-id")).toHaveTextContent("id · 12345678");
    expect(screen.getByTestId("loop-status-chip-stop")).toBeInTheDocument();
  });
});
