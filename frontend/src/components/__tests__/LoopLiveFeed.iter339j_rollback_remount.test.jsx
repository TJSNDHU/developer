/**
 * Iter 339j — prod bug regression: rollback clicks logged but the POST
 * never fired because component state ("confirming") was lost between
 * clicks (remount / state reset). The confirm-arm now lives in a
 * module-level Map, so a remount between click 1 and click 2 must NOT
 * prevent the POST from firing.
 */
import React from "react";
import { render, fireEvent, cleanup, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

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

describe("Iter 339j — rollback survives remount between clicks", () => {
  beforeEach(() => { rollbackLoopMock.mockClear(); });

  it("click 1 → REMOUNT → click 2 still fires POST /rollback", async () => {
    const { unmount } = render(
      <ShippedRow loopId="prod-remount-1" ship={ship} onRollbackStarted={() => {}} />,
    );
    fireEvent.click(document.querySelector('[data-testid="loop-shipped-rollback-btn-cc60342"]'));
    expect(rollbackLoopMock).not.toHaveBeenCalled();

    // Simulate the prod remount that wiped component state.
    unmount();
    render(
      <ShippedRow loopId="prod-remount-1" ship={ship} onRollbackStarted={() => {}} />,
    );

    // Fresh instance renders phase=idle ("Rollback" label), but the
    // module-level arm registry remembers click 1 — this click FIRES.
    await act(async () => {
      fireEvent.click(document.querySelector('[data-testid="loop-shipped-rollback-btn-cc60342"]'));
    });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    expect(rollbackLoopMock).toHaveBeenCalledWith("prod-remount-1");
    cleanup();
  });

  it("two clicks on a stable instance still fire exactly one POST", async () => {
    render(
      <ShippedRow loopId="prod-stable-1" ship={ship} onRollbackStarted={() => {}} />,
    );
    const btn = () => document.querySelector('[data-testid^="loop-shipped-rollback-btn"]');
    fireEvent.click(btn());
    expect(rollbackLoopMock).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(btn()); });
    expect(rollbackLoopMock).toHaveBeenCalledTimes(1);
    cleanup();
  });
});
