/**
 * LoopStatusChip.iter329_fixC_terminal_label.test.jsx
 *
 * Iter 329 · Fix C · Bug 1 — top chip stuck on "LOOP · SHIPPING" after
 * ship-success (real founder-observed bug on commit 0b79db0). Chip
 * should transition to "LOOP · SHIPPED" during the 30s terminal
 * grace window.
 *
 * Root cause: `phaseText()` picks `active.phase` before `active.state`.
 * On terminal snapshot the backend `/loop/{id}/status` returns:
 *   state = "completed"
 *   phase = "ship"          ← last mid-loop phase
 * PHASE_LABEL["ship"] = "SHIPPING", so the chip renders "SHIPPING"
 * for the entire grace window instead of "SHIPPED".
 *
 * Fix: when `state` is a known terminal state (completed/done/shipped/
 * failed/aborted/expired), it MUST win over `phase` in the label
 * derivation.
 *
 * These tests exercise the phaseText helper directly + a full-render
 * DOM check via mocked getActiveLoop / getLoopStatus.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// Mock the loopApi helpers BEFORE the component import so the mock
// is applied to the module the component captures.
vi.mock("../../lib/loopApi", () => ({
  getActiveLoop:  vi.fn(),
  getLoopStatus:  vi.fn(),
  cancelLoop:     vi.fn(),
}));

import LoopStatusChip, { __testables__ } from "../LoopStatusChip.jsx";
import * as loopApi from "../../lib/loopApi";


describe("Iter 329 · Fix C · Bug 1 — LoopStatusChip terminal label wins over phase", () => {
  describe("phaseText helper — unit tests", () => {
    const { phaseText } = __testables__;

    it("state=completed + phase=ship → SHIPPED (not SHIPPING) — exact founder-observed bug", () => {
      expect(phaseText({ state: "completed", phase: "ship" })).toBe("SHIPPED");
    });

    it("state=done + phase=execute → SHIPPED (not EXECUTING)", () => {
      expect(phaseText({ state: "done", phase: "execute" })).toBe("SHIPPED");
    });

    it("state=failed + phase=execute → FAILED (not EXECUTING)", () => {
      expect(phaseText({ state: "failed", phase: "execute" })).toBe("FAILED");
    });

    it("state=aborted + phase=verify → ABORTED (not VERIFYING)", () => {
      expect(phaseText({ state: "aborted", phase: "verify" })).toBe("ABORTED");
    });

    it("state=expired + phase=scan → EXPIRED (not SECURITY SCAN)", () => {
      expect(phaseText({ state: "expired", phase: "scan" })).toBe("EXPIRED");
    });

    it("REGRESSION — running loop: state=running + phase=executing → EXECUTING (phase wins)", () => {
      expect(phaseText({ state: "running", phase: "executing" })).toBe("EXECUTING");
    });

    it("REGRESSION — awaiting_confirmation preserves state-wins-for-approvals rule", () => {
      expect(phaseText({ state: "awaiting_confirmation", phase: "plan" }))
        .toBe("AWAITING APPROVAL");
    });

    it("REGRESSION — paused_for_user preserves state-wins-for-paused rule", () => {
      expect(phaseText({ state: "paused_for_user", phase: "execute" }))
        .toBe("PAUSED · YOUR INPUT");
    });

    it("REGRESSION — null active → IDLE", () => {
      expect(phaseText(null)).toBe("IDLE");
    });
  });

  describe("full-render DOM tests", () => {
    beforeEach(() => { vi.clearAllMocks(); });
    afterEach(() => { vi.clearAllMocks(); });

    // The chip needs a running loop first so lastActiveRef gets set,
    // then a subsequent poll returns null + getLoopStatus reports the
    // true terminal state. We use fake timers to advance past
    // POLL_MS.
    async function runningThenTerminal({ termState, termPhase }) {
      loopApi.getActiveLoop.mockResolvedValueOnce({
        active: { loop_id: "loop_iter329_fixC_test",
                  state: "shipping", phase: "ship" },
      });
      const view = render(<LoopStatusChip projectId={null} />);
      await waitFor(() => {
        expect(screen.getByTestId("loop-status-chip-phase"))
          .toBeInTheDocument();
      });

      // Second poll: /loop/active returns null (terminal), status
      // probe returns the true terminal doc.
      loopApi.getActiveLoop.mockResolvedValue({ active: null });
      loopApi.getLoopStatus.mockResolvedValue({
        state: termState,
        phase: termPhase,
        context: { commit: { sha: "0b79db0" } },
      });
      // Force a rerender (component polls on mount + on prop-driven
      // effects; the poll function itself is stable so we re-invoke
      // via focus event).
      window.dispatchEvent(new Event("focus"));
      return view;
    }

    it("SHIP-SUCCESS terminal grace: chip renders SHIPPED (not SHIPPING) — founder-observed bug lock-in", async () => {
      await runningThenTerminal({ termState: "completed", termPhase: "ship" });
      await waitFor(() => {
        expect(screen.getByTestId("loop-status-chip-phase"))
          .toHaveTextContent(/SHIPPED/i);
      }, { timeout: 2000 });
      expect(screen.getByTestId("loop-status-chip-phase"))
        .not.toHaveTextContent(/SHIPPING/i);
    });

    it("FAILED-AT-EXECUTE terminal: chip renders FAILED (not EXECUTING)", async () => {
      await runningThenTerminal({ termState: "failed", termPhase: "execute" });
      await waitFor(() => {
        expect(screen.getByTestId("loop-status-chip-phase"))
          .toHaveTextContent(/FAILED/i);
      }, { timeout: 2000 });
      expect(screen.getByTestId("loop-status-chip-phase"))
        .not.toHaveTextContent(/EXECUTING/i);
    });
  });
});
