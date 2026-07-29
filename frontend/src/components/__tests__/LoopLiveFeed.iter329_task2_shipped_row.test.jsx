/**
 * LoopLiveFeed.iter329_task2_shipped_row.test.jsx
 *
 * Iter 329 · Task 2 — inline Shipped row (replaces dark-overlay
 * ShipConfirmModal for loop-mode ships).
 *
 * Locks in:
 *   1. extractShipInfo pure helper — extracts commit_sha / html_url /
 *      files from the terminal state=completed · phase=ship event.
 *   2. ShippedRow renders "Shipped {sha7} · View on GitHub · Rollback"
 *      when terminal=true and the event stream contains a ship event.
 *   3. Rollback flow (Iter 330 Path P1): idle → confirming (2-click
 *      safety) → submitting (POST in flight) → handed-off (SSE
 *      progress owned by OperationHistory; poll phases removed).
 *   4. Rollback error surface.
 *   5. Non-terminal / no-ship-event states DO NOT render the row.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";

vi.mock("../../lib/loopApi", () => ({
  rollbackLoop:  vi.fn(),
  getLoopStatus: vi.fn(),
  // Iter 331 — OperationHistory (mounted when projectId is passed)
  // opens its own SSE subscription via streamLoopEvents on hand-off.
  streamLoopEvents: vi.fn(() => ({ abort: vi.fn() })),
}));

import LoopLiveFeed, { extractShipInfo } from "../LoopLiveFeed.jsx";
import * as loopApi from "../../lib/loopApi";


function shipEvent({ commit_sha = "abcdef123", full_sha, html_url, files, commit_message } = {}) {
  return {
    state: "completed",
    phase: "ship",
    data: {
      type: "state",
      commit_sha,
      full_sha: full_sha || commit_sha,
      html_url: html_url || `https://github.com/tj/repo/commit/${commit_sha}`,
      files_changed: files || ["ROLLBACKTEST.md"],
      commit_message: commit_message || "auto-commit",
    },
  };
}
function narrationEvent(text, corr, tsEpoch, tone = "pending", state) {
  return {
    state,
    data: {
      type: "narration", tone,
      narration_step: "execute",
      narration_text: text,
      correlation_id: corr,
      ts_epoch: tsEpoch,
    },
  };
}

// Same 2-effect mount race workaround used by Fix B tests — mount
// with event=null so the loopId-reset effect commits first, then
// rerender in a rerender-stream.
function feedMount(props) {
  const { event, ...rest } = props;
  let handle;
  act(() => { handle = render(<LoopLiveFeed {...rest} event={null} />); });
  if (event) {
    act(() => { handle.rerender(<LoopLiveFeed {...rest} event={event} />); });
  }
  return handle;
}
function feedRerender(handle, props) {
  act(() => { handle.rerender(<LoopLiveFeed {...props} />); });
}


describe("Iter 329 · Task 2 — extractShipInfo helper", () => {
  it("returns null when events is empty", () => {
    expect(extractShipInfo([])).toBeNull();
  });

  it("returns null when no ship event present", () => {
    const events = [narrationEvent("Planning…", "c1", 100)];
    expect(extractShipInfo(events)).toBeNull();
  });

  it("returns null on completed non-ship phase", () => {
    const events = [{ state: "completed", phase: "verify",
                      data: { commit_sha: "x", type: "state" } }];
    expect(extractShipInfo(events)).toBeNull();
  });

  it("returns null on ship phase but non-terminal state", () => {
    const events = [{ state: "shipping", phase: "ship",
                      data: { commit_sha: "x", type: "state" } }];
    expect(extractShipInfo(events)).toBeNull();
  });

  it("extracts on completed+ship with commit_sha (canonical shape)", () => {
    const events = [shipEvent({ commit_sha: "1f70444abcd", html_url: "https://github.com/x/y/commit/1f70444abcd" })];
    const info = extractShipInfo(events);
    expect(info).not.toBeNull();
    expect(info.commitSha).toBe("1f70444abcd");
    expect(info.shortSha).toBe("1f70444");
    expect(info.htmlUrl).toBe("https://github.com/x/y/commit/1f70444abcd");
    expect(info.files).toEqual(["ROLLBACKTEST.md"]);
  });

  it("returns latest ship event when multiple exist (newest wins)", () => {
    const events = [
      shipEvent({ commit_sha: "old_sha_1234567" }),
      shipEvent({ commit_sha: "new_sha_7654321" }),
    ];
    const info = extractShipInfo(events);
    expect(info.commitSha).toBe("new_sha_7654321");
  });
});


describe("Iter 329 · Task 2 — ShippedRow render + rollback flow", () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(() => { vi.clearAllMocks(); });

  it("Terminal + no ship-event → row is NOT rendered", () => {
    // e.g. aborted or failed loop — no shipped row should appear.
    feedMount({
      loopId: "loop_task2_b",
      event: { state: "aborted", data: { type: "state" } },
      terminal: true,
    });
    expect(screen.queryByTestId(/loop-shipped-row-/)).toBeNull();
  });

  it("shipInfo present + terminal=false → ROW STILL RENDERS (Fix A · impossible-state test replaced)", () => {
    // Iter 329 · Fix C · Bug X — the original test asserted "non-
    // terminal + ship-event → NOT rendered". Server-side invariant
    // (loop_engine.py 2823-2944) confirms `data.commit_sha` can
    // only exist in a `state=completed, phase=ship` event AFTER a
    // real GitHub push succeeds. So the old defensive gate was
    // guarding against an impossible backend state — and worse, it
    // coupled the row's mount to the parent's `terminal` prop,
    // causing the row to unmount on unrelated re-renders (bug X)
    // and destroying the confirm-click state machine. Fix A drops
    // the `terminal` gate. This test locks that in: ANY event with
    // a `commit_sha`-carrying ship shape MUST render the row,
    // independent of the `terminal` prop's current value.
    feedMount({
      loopId: "loop_task2_fixa",
      event: shipEvent({ commit_sha: "abcdef1fixa" }),
      terminal: false,
    });
    expect(screen.getByTestId("loop-shipped-row-abcdef1")).toBeInTheDocument();
  });

  it("Terminal + ship-event → row renders with sha7, GitHub link, Rollback button", () => {
    feedMount({
      loopId: "loop_task2_c",
      event: shipEvent({ commit_sha: "5d939a4abcd" }),
      terminal: true,
    });
    const row = screen.getByTestId("loop-shipped-row-5d939a4");
    expect(row).toBeInTheDocument();
    expect(row.getAttribute("data-rollback-phase")).toBe("idle");
    expect(screen.getByTestId("loop-shipped-label-5d939a4"))
      .toHaveTextContent(/Shipped\s+5d939a4/);
    const link = screen.getByTestId("loop-shipped-github-5d939a4");
    expect(link.getAttribute("href")).toContain("5d939a4");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(screen.getByTestId("loop-shipped-rollback-btn-5d939a4"))
      .toHaveTextContent(/Rollback/i);
  });

  it("Rollback flow (Iter 342): pointerdown + confirm → submitting → handed-off", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    let resolvePost;
    loopApi.rollbackLoop.mockImplementation(
      () => new Promise((res) => { resolvePost = res; }),
    );
    const onRollbackStarted = vi.fn();

    feedMount({
      loopId: "loop_task2_d",
      projectId: "proj_task2_d",
      onRollbackStarted,
      event: shipEvent({ commit_sha: "5d939a4abcd" }),
      terminal: true,
    });
    const btn = screen.getByTestId("loop-shipped-rollback-btn-5d939a4");

    // Single press → native confirm → POST fires; in flight = submitting.
    const { fireEvent } = await import("@testing-library/react");
    await act(async () => { fireEvent.pointerDown(btn, { button: 0 }); });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(loopApi.rollbackLoop).toHaveBeenCalledWith("loop_task2_d");
    expect(screen.getByTestId("loop-shipped-row-5d939a4")
      .getAttribute("data-rollback-phase")).toBe("submitting");
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent(/Rolling back/);

    // POST resolves → terminal "handed-off"; button stays disabled
    // and points the user at OperationHistory.
    await act(async () => {
      resolvePost({ ok: true, loop_id: "loop_task2_d", rollback_status: "queued" });
    });
    await waitFor(() => {
      expect(screen.getByTestId("loop-shipped-row-5d939a4")
        .getAttribute("data-rollback-phase")).toBe("handed-off");
    });
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent(/see history/i);

    // Hand-off proof — Iter 339l: LoopLiveFeed forwards the loopId to
    // the PARENT via onRollbackStarted (ChatPanel opens the single
    // Ops History panel, which owns the /stream subscription now).
    await waitFor(() => {
      expect(onRollbackStarted).toHaveBeenCalledWith("loop_task2_d");
    });
    confirmSpy.mockRestore();
  });

  it("Rollback POST rejects → phase=failed + error text surfaces", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    loopApi.rollbackLoop.mockRejectedValue(
      new Error("Only completed loops can be rolled back (current: aborted)"),
    );
    feedMount({
      loopId: "loop_task2_e",
      event: shipEvent({ commit_sha: "abcdef1234" }),
      terminal: true,
    });
    const { fireEvent } = await import("@testing-library/react");
    const btn = screen.getByTestId("loop-shipped-rollback-btn-abcdef1");
    await act(async () => { fireEvent.pointerDown(btn, { button: 0 }); });
    await waitFor(() => {
      expect(screen.getByTestId("loop-shipped-row-abcdef1")
        .getAttribute("data-rollback-phase")).toBe("failed");
    });
    expect(screen.getByTestId("loop-shipped-rollback-error"))
      .toHaveTextContent(/Only completed loops/);
    confirmSpy.mockRestore();
  });

  it("Iter 342 — confirm Cancel leaves row idle, no POST", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    feedMount({
      loopId: "loop_task2_f",
      event: shipEvent({ commit_sha: "cancelsha99" }),
      terminal: true,
    });
    const { fireEvent } = await import("@testing-library/react");
    const btn = screen.getByTestId("loop-shipped-rollback-btn-cancels");
    await act(async () => { fireEvent.pointerDown(btn, { button: 0 }); });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(loopApi.rollbackLoop).not.toHaveBeenCalled();
    expect(screen.getByTestId("loop-shipped-row-cancels")
      .getAttribute("data-rollback-phase")).toBe("idle");
    confirmSpy.mockRestore();
  });
});
