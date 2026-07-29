/**
 * Iter 339j → Iter 342 — prod bug regression (3rd recurrence): rollback
 * clicks produced ZERO network calls. Root cause class: ANY multi-click
 * confirm flow depending on React state/timers is fragile under SSE
 * remount churn (the browser can even eat `click` when the node is
 * replaced between mousedown and mouseup).
 *
 * New contract locked in here:
 *   • `pointerdown` alone triggers the flow (fires pre-remount).
 *   • Native window.confirm() is the safety gate — synchronous,
 *     remount-immune.
 *   • confirm OK  → POST /rollback fires IMMEDIATELY.
 *   • confirm Cancel → no POST.
 *   • The synthetic `click` after a handled `pointerdown` is swallowed
 *     (exactly one POST).
 *   • Module-level in-flight registry blocks re-fires across remounts.
 */
import React from "react";
import { render, fireEvent, cleanup, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const rollbackLoopMock = vi.fn(() => Promise.resolve({ ok: true }));
vi.mock("../../lib/loopApi", () => ({
  rollbackLoop: (...a) => rollbackLoopMock(...a),
  streamLoopEvents: vi.fn(() => ({ abort: () => {} })),
}));

import { ShippedRow } from "../LoopLiveFeed";

const ship = {
  shortSha: "cc60342",
  fullSha: "cc60342".padEnd(40, "a"),
  htmlUrl: "https://github.com/x/y/commit/cc60342",
};

const btn = () =>
  document.querySelector('[data-testid="loop-shipped-rollback-btn-cc60342"]');

describe("Iter 342 — pointerdown + native confirm rollback", () => {
  let confirmSpy;
  beforeEach(() => {
    rollbackLoopMock.mockClear();
    confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  });
  afterEach(() => {
    confirmSpy.mockRestore();
    cleanup();
  });

  it("single pointerdown + confirm OK → POST fires immediately", async () => {
    render(
      <ShippedRow loopId="i342-a" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    expect(rollbackLoopMock).toHaveBeenCalledWith("i342-a");
  });

  it("confirm Cancel → NO POST", async () => {
    confirmSpy.mockReturnValue(false);
    render(
      <ShippedRow loopId="i342-b" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(rollbackLoopMock).not.toHaveBeenCalled();
  });

  it("pointerdown followed by its synthetic click → exactly ONE POST", async () => {
    render(
      <ShippedRow loopId="i342-c" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
      fireEvent.click(btn());
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
  });

  it("REMOUNT between render and press cannot eat the action (fresh instance still fires)", async () => {
    const { unmount } = render(
      <ShippedRow loopId="i342-d" ship={ship} onRollbackStarted={() => {}} />,
    );
    unmount();
    render(
      <ShippedRow loopId="i342-d" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    expect(rollbackLoopMock).toHaveBeenCalledWith("i342-d");
  });

  it("in-flight guard survives remount — second press after success does NOT re-POST", async () => {
    const { unmount } = render(
      <ShippedRow loopId="i342-e" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    unmount();
    render(
      <ShippedRow loopId="i342-e" ship={ship} onRollbackStarted={() => {}} />,
    );
    // Remounted instance resyncs phase from the module registry.
    expect(btn().disabled).toBe(true);
    await act(async () => {
      fireEvent.pointerDown(btn(), { button: 0 });
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
  });

  it("keyboard activation (click without pointerdown) still works", async () => {
    render(
      <ShippedRow loopId="i342-f" ship={ship} onRollbackStarted={() => {}} />,
    );
    await act(async () => {
      fireEvent.click(btn());
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    expect(rollbackLoopMock).toHaveBeenCalledWith("i342-f");
  });
});
