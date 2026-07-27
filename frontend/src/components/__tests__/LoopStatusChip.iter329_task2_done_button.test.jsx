/**
 * LoopStatusChip.iter329_task2_done_button.test.jsx
 *
 * Iter 329 · Task 2 — inline "Done" affordance on the chip during
 * the terminal-success grace window. Replaces the dark-overlay
 * modal's Close button.
 *
 * Invariants:
 *   • Done button only rendered when isTerminal && !isTerminalFailure.
 *   • Clicking Done clears terminalSnapshot immediately (chip unmounts
 *     via the existing `!active && !terminalSnapshot` gate).
 *   • Done button is NOT rendered on failed/aborted/expired terminals
 *     (those still show the amber/red label during grace).
 *   • Done button is NOT rendered while the loop is running.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";

vi.mock("../../lib/loopApi", () => ({
  getActiveLoop:  vi.fn(),
  getLoopStatus:  vi.fn(),
  cancelLoop:     vi.fn(),
}));

import LoopStatusChip from "../LoopStatusChip.jsx";
import * as loopApi from "../../lib/loopApi";


async function runningThenTerminal({ termState, termPhase }) {
  loopApi.getActiveLoop.mockResolvedValueOnce({
    active: { loop_id: "loop_task2_chip",
              state: "shipping", phase: "ship" },
  });
  const view = render(<LoopStatusChip projectId={null} />);
  await waitFor(() => {
    expect(screen.getByTestId("loop-status-chip-phase")).toBeInTheDocument();
  });
  loopApi.getActiveLoop.mockResolvedValue({ active: null });
  loopApi.getLoopStatus.mockResolvedValue({
    state: termState, phase: termPhase,
    context: { commit: { sha: "5d939a4" } },
  });
  window.dispatchEvent(new Event("focus"));
  return view;
}

describe("Iter 329 · Task 2 — LoopStatusChip Done button", () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(() => { vi.clearAllMocks(); });

  it("Running loop: Done button is NOT rendered (Stop button is)", async () => {
    loopApi.getActiveLoop.mockResolvedValue({
      active: { loop_id: "loop_running", state: "executing", phase: "execute" },
    });
    render(<LoopStatusChip projectId={null} />);
    await waitFor(() => {
      expect(screen.getByTestId("loop-status-chip-stop")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("loop-status-chip-done")).toBeNull();
  });

  it("Terminal-success: Done button renders alongside SHIPPED label", async () => {
    await runningThenTerminal({ termState: "completed", termPhase: "ship" });
    await waitFor(() => {
      expect(screen.getByTestId("loop-status-chip-phase"))
        .toHaveTextContent(/SHIPPED/i);
    });
    expect(screen.getByTestId("loop-status-chip-done")).toBeInTheDocument();
    // Stop button MUST NOT render on terminal (would be nonsensical).
    expect(screen.queryByTestId("loop-status-chip-stop")).toBeNull();
  });

  it("Terminal-failure (failed): Done button is NOT rendered", async () => {
    await runningThenTerminal({ termState: "failed", termPhase: "execute" });
    await waitFor(() => {
      expect(screen.getByTestId("loop-status-chip-phase"))
        .toHaveTextContent(/FAILED/i);
    });
    // Failed loop keeps the label visible for the full grace window;
    // no Done affordance so the founder can't accidentally dismiss
    // failure context.
    expect(screen.queryByTestId("loop-status-chip-done")).toBeNull();
  });

  it("Terminal-failure (aborted): Done button is NOT rendered", async () => {
    await runningThenTerminal({ termState: "aborted", termPhase: "execute" });
    await waitFor(() => {
      expect(screen.getByTestId("loop-status-chip-phase"))
        .toHaveTextContent(/ABORTED/i);
    });
    expect(screen.queryByTestId("loop-status-chip-done")).toBeNull();
  });

  it("Terminal-success + Done click → chip unmounts immediately", async () => {
    await runningThenTerminal({ termState: "completed", termPhase: "ship" });
    await waitFor(() => {
      expect(screen.getByTestId("loop-status-chip-done")).toBeInTheDocument();
    });
    const doneBtn = screen.getByTestId("loop-status-chip-done");
    await act(async () => { doneBtn.click(); });
    // Chip's parent div should be gone (returns null when no active
    // and no terminalSnapshot).
    await waitFor(() => {
      expect(screen.queryByTestId("loop-status-chip")).toBeNull();
    });
  });
});
