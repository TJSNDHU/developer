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

  it("Rollback flow: idle → confirming → submitting → handed-off (Iter 330 SSE hand-off)", async () => {
    // Iter 331 — rewritten for the Iter 330 Path P1 state machine.
    // Old poll-based phases (queued/running/done via getLoopStatus)
    // were removed: ShippedRow POSTs /rollback, holds "submitting"
    // while in flight, then terminal "handed-off" on success and
    // lifts the loopId into OperationHistory (which owns SSE progress).
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

    // First click → confirming
    await act(async () => { btn.click(); });
    expect(screen.getByTestId("loop-shipped-row-5d939a4")
      .getAttribute("data-rollback-phase")).toBe("confirming");
    expect(btn).toHaveTextContent(/Confirm rollback/i);

    // Second click → POST fires; while in flight phase = submitting.
    await act(async () => { btn.click(); });
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
  });

  it("Rollback POST rejects → phase=failed + error text surfaces", async () => {
    loopApi.rollbackLoop.mockRejectedValue(
      new Error("Only completed loops can be rolled back (current: aborted)"),
    );
    feedMount({
      loopId: "loop_task2_e",
      event: shipEvent({ commit_sha: "abcdef1234" }),
      terminal: true,
    });
    const btn = screen.getByTestId("loop-shipped-rollback-btn-abcdef1");
    await act(async () => { btn.click(); });   // confirming
    await act(async () => { btn.click(); });   // fire
    await waitFor(() => {
      expect(screen.getByTestId("loop-shipped-row-abcdef1")
        .getAttribute("data-rollback-phase")).toBe("failed");
    });
    expect(screen.getByTestId("loop-shipped-rollback-error"))
      .toHaveTextContent(/Only completed loops/);
  });

  it("Iter 329 · Fix C — REAL-TIMER two-click rollback (locks in the founder-reported production bug)", async () => {
    // ── The exact class of test that was missing ─────────────────
    // Previous tests used act() with instant clicks → passed against
    // synchronous state flushes. Production failed because real
    // timing + React 18 concurrent scheduling + parent re-renders
    // between clicks caused stale-closure OR unmount races.
    //
    // This test uses REAL setTimeout (no vi.useFakeTimers) with
    // real wall-clock delays between clicks — 1.5s wait between
    // click 1 and click 2 — matching the founder's multi-second
    // manual retry pattern. Asserts:
    //   (a) After click 1: data-rollback-phase="confirming", the
    //       button label contains "Confirm rollback" (visual
    //       feedback is real), the phase-specific testid variant
    //       is present.
    //   (b) After the real wait + click 2: rollbackLoop() actually
    //       fires with the correct loop_id, phase advances past
    //       "confirming".
    loopApi.rollbackLoop.mockResolvedValue({
      ok: true, loop_id: "loop_realtimer_test",
      rollback_status: "queued",
    });
    loopApi.getLoopStatus.mockResolvedValue({
      rollback_status: "done",
      rollback_sha: "revert123456789",
    });

    feedMount({
      loopId: "loop_realtimer_test",
      event: shipEvent({ commit_sha: "originaLsha1" }),
      terminal: true,
    });
    const initialBtn = screen.getByTestId("loop-shipped-rollback-btn-origina");

    // ── Click 1 (real click, real state flush) ───────────────────
    await act(async () => { initialBtn.click(); });
    // Assert VISIBLE feedback landed — this is what the founder
    // couldn't see in production.
    const row = screen.getByTestId("loop-shipped-row-origina");
    expect(row.getAttribute("data-rollback-phase")).toBe("confirming");
    const confirmingBtn = screen.getByTestId(
      "loop-shipped-rollback-btn-confirming-origina",
    );
    expect(confirmingBtn).toBeInTheDocument();
    expect(confirmingBtn).toHaveTextContent(/Confirm rollback/i);
    expect(confirmingBtn.getAttribute("aria-pressed")).toBe("true");

    // ── Real 1.5s wait — no fake timers, no shortcuts ───────────
    // This is the interval that mattered on production. The
    // callback MUST still see phase=confirming after this real
    // wall-clock gap and any interleaved React re-renders that
    // may occur.
    await new Promise((resolve) => setTimeout(resolve, 1500));

    // ── Click 2 (real click after real wait) ─────────────────────
    await act(async () => { confirmingBtn.click(); });

    // The POST must have fired with the correct loop_id.
    expect(loopApi.rollbackLoop).toHaveBeenCalledTimes(1);
    expect(loopApi.rollbackLoop).toHaveBeenCalledWith("loop_realtimer_test");

    // Phase must have advanced past confirming. Iter 331 — the
    // Iter 330 refactor collapsed the poll-derived queued/running/done
    // phases into submitting (POST in flight) → handed-off (terminal).
    await waitFor(() => {
      const currentPhase = screen.getByTestId("loop-shipped-row-origina")
        .getAttribute("data-rollback-phase");
      expect(["submitting", "handed-off"]).toContain(currentPhase);
    }, { timeout: 3000 });
  });

  it("Iter 329 · Fix C — confirm timer auto-reverts idle after 10s (regression guard)", async () => {
    // The confirm window bumped 4s → 10s. The revert-to-idle path
    // must still fire; also must NOT fire if user already advanced
    // to queued/running before the window closed.
    vi.useFakeTimers();
    try {
      feedMount({
        loopId: "loop_confirm_revert",
        event: shipEvent({ commit_sha: "revertshsh" }),  // sha7=reverts
        terminal: true,
      });
      const btn = screen.getByTestId("loop-shipped-rollback-btn-reverts");
      await act(async () => { btn.click(); });
      expect(screen.getByTestId("loop-shipped-row-reverts")
        .getAttribute("data-rollback-phase")).toBe("confirming");
      // At 9.9s: still confirming.
      await act(async () => { vi.advanceTimersByTime(9_900); });
      expect(screen.getByTestId("loop-shipped-row-reverts")
        .getAttribute("data-rollback-phase")).toBe("confirming");
      // At 10.1s: reverted to idle.
      await act(async () => { vi.advanceTimersByTime(200); });
      expect(screen.getByTestId("loop-shipped-row-reverts")
        .getAttribute("data-rollback-phase")).toBe("idle");
    } finally {
      vi.useRealTimers();
    }
  });
});
