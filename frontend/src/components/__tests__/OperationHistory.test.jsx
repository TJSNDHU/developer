/**
 * OperationHistory.test.jsx — Iter 331 · regression suite for the
 * Iter 330 Path P1 rollback timeline component.
 *
 * Mirrors the manual /dev harness assertions that verified the
 * StrictMode/microtask dedupe fix:
 *   1. History hydration — GET /loop/history rows render collapsed.
 *   2. Guard A — prop-stable parent re-render churn opens exactly
 *      ONE stream (React.memo + narrow effect deps).
 *   3. Guard B/C — post-terminal reopen with the SAME loopId never
 *      re-subscribes (handledLoopIdsRef).
 *   4. Live rollback stream → expanded op with step statuses →
 *      terminal completed → collapsed row + stream aborted.
 *      Non-rollback phases (ship) are ignored by this timeline.
 *   5. Dedupe — seed history row + live finalize for the same
 *      (loop_id, op_type) REPLACES, never duplicates.
 *   6. Error fallback — history fetch failure is fail-open.
 *   7. Stream onError is non-fatal and does not finalize.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";

// OperationHistory reads REACT_APP_BACKEND_URL at module import time —
// hoist the env stub so API_BASE is non-empty and the history fetch runs.
vi.hoisted(() => { process.env.REACT_APP_BACKEND_URL = "http://vitest.local"; });

vi.mock("../../lib/loopApi", () => ({
  streamLoopEvents: vi.fn(),
}));

import OperationHistory from "../OperationHistory.jsx";
import * as loopApi from "../../lib/loopApi";

// ── Harness ─────────────────────────────────────────────────────────
let subs; // captured { loopId, cb, abort } per streamLoopEvents call

function seedHistory(items) {
  global.fetch = vi.fn(() => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ items }),
  }));
}

function rbEvent(state, step, msg, extra = {}) {
  return {
    phase: "rollback", state, step, total_steps: 3,
    message: msg, timestamp: "2026-07-28T05:00:00Z",
    data: extra,
  };
}

beforeEach(() => {
  subs = [];
  loopApi.streamLoopEvents.mockImplementation((loopId, cb) => {
    const sub = { loopId, cb, abort: vi.fn() };
    subs.push(sub);
    return { abort: sub.abort };
  });
});
afterEach(() => { vi.clearAllMocks(); });


describe("Iter 331 · OperationHistory regression suite", () => {
  it("1. renders collapsed rows from GET /loop/history on mount", async () => {
    seedHistory([
      { loop_id: "loopship01", op_type: "ship", state: "completed",
        all_passed: true, step_count: 5,
        started_at: "2026-07-27T10:00:00Z", finished_at: "2026-07-27T10:02:00Z" },
      { loop_id: "looprb0001", op_type: "rollback", state: "completed",
        all_passed: true, step_count: 3,
        started_at: "2026-07-27T11:00:00Z", finished_at: "2026-07-27T11:01:00Z" },
    ]);
    render(<OperationHistory projectId="p1" activeLoopId={null} />);
    await waitFor(() => {
      expect(screen.getByTestId("op-history-row-collapsed-ship-loopship"))
        .toBeInTheDocument();
    });
    expect(screen.getByTestId("op-history-row-collapsed-rollback-looprb00"))
      .toBeInTheDocument();
    expect(screen.getByText(/Ship finished/)).toBeInTheDocument();
    expect(screen.getByText(/Rollback finished/)).toBeInTheDocument();
    // No activeLoopId → no SSE subscription.
    expect(loopApi.streamLoopEvents).not.toHaveBeenCalled();
  });

  it("2. Guard A — prop-stable parent re-render churn opens exactly ONE stream", async () => {
    seedHistory([]);
    const { rerender } = render(
      <OperationHistory projectId="p1" activeLoopId="loop_a" />,
    );
    for (let i = 0; i < 5; i++) {
      rerender(<OperationHistory projectId="p1" activeLoopId="loop_a" />);
    }
    expect(loopApi.streamLoopEvents).toHaveBeenCalledTimes(1);
    expect(loopApi.streamLoopEvents)
      .toHaveBeenCalledWith("loop_a", expect.any(Object));
  });

  it("3. Guard B/C — post-terminal same loopId never reopens the stream", async () => {
    seedHistory([]);
    const { rerender } = render(
      <OperationHistory projectId="p1" activeLoopId="loop_b" />,
    );
    expect(subs).toHaveLength(1);
    // Flush the mount-time history fetch (setHistory([])) BEFORE any
    // live event — mirrors production ordering where hydration lands
    // long before rollback frames.
    await act(async () => {});
    await act(async () => {
      subs[0].cb.onEvent(rbEvent("running", 1, "Reverting commit…"));
      subs[0].cb.onEvent(rbEvent("completed", 3, "Rollback finished",
        { commit_sha: "ea3ebcf9876" }));
      await Promise.resolve(); // flush the microtask-deferred finalize
    });
    await waitFor(() => {
      expect(screen.getByTestId("op-history-row-collapsed-rollback-loop_b"))
        .toBeInTheDocument();
    });
    expect(subs[0].abort).toHaveBeenCalled();
    // Parent churn re-fires the effect with the SAME loopId.
    rerender(<OperationHistory projectId="p1" activeLoopId={null} />);
    rerender(<OperationHistory projectId="p1" activeLoopId="loop_b" />);
    expect(loopApi.streamLoopEvents).toHaveBeenCalledTimes(1);
  });

  it("4. live rollback stream — expanded steps, ship-phase ignored, terminal collapse", async () => {
    seedHistory([]);
    render(<OperationHistory projectId="p1" activeLoopId="loop_c" />);
    await act(async () => {}); // flush mount-time history fetch
    // Ship-phase events do NOT feed this timeline.
    await act(async () => {
      subs[0].cb.onEvent({ phase: "ship", state: "completed", step: 5,
        total_steps: 5, message: "Shipped", data: {} });
    });
    expect(screen.queryByTestId("op-history-expanded-rollback-loop_c")).toBeNull();

    await act(async () => {
      subs[0].cb.onEvent(rbEvent("running", 1, "Locating ship commit…"));
    });
    const expanded = screen.getByTestId("op-history-expanded-rollback-loop_c");
    expect(expanded.getAttribute("data-op-state")).toBe("running");
    expect(screen.getByText("Locating ship commit…")).toBeInTheDocument();

    await act(async () => {
      subs[0].cb.onEvent(rbEvent("running", 2, "Creating revert commit…"));
    });
    // Step 1 auto-resolves to done once step 2 is in progress.
    expect(screen.getByTestId("op-step-loop_c-0")).toHaveTextContent("✓");

    await act(async () => {
      subs[0].cb.onEvent(rbEvent("completed", 3, "Rollback complete",
        { commit_sha: "ea3ebcf9876" }));
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.queryByTestId("op-history-expanded-rollback-loop_c")).toBeNull();
    });
    const row = screen.getByTestId("op-history-row-collapsed-rollback-loop_c");
    expect(row.getAttribute("data-op-state")).toBe("completed");
  });

  it("5. dedupe — seed row + live finalize for same (loop_id, op_type) never duplicates", async () => {
    seedHistory([
      { loop_id: "loop_dd", op_type: "rollback", state: "running",
        all_passed: false, step_count: 3,
        started_at: "2026-07-28T04:00:00Z" },
    ]);
    render(<OperationHistory projectId="p1" activeLoopId="loop_dd" />);
    await waitFor(() => {
      expect(screen.getByTestId("op-history-row-collapsed-rollback-loop_dd"))
        .toBeInTheDocument();
    });
    await act(async () => {
      subs[0].cb.onEvent(rbEvent("completed", 3, "Rollback complete"));
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.getByTestId("op-history-row-collapsed-rollback-loop_dd")
        .getAttribute("data-op-state")).toBe("completed");
    });
    expect(screen.getAllByTestId("op-history-row-collapsed-rollback-loop_dd"))
      .toHaveLength(1);
  });

  it("6. error fallback — history fetch failure is fail-open (no crash, empty list)", async () => {
    global.fetch = vi.fn(() => Promise.reject(new Error("network down")));
    render(<OperationHistory projectId="p1" activeLoopId={null} />);
    await act(async () => { await Promise.resolve(); });
    const root = screen.getByTestId("operation-history-root");
    expect(root).toBeInTheDocument();
    expect(root.children.length).toBe(0);
    expect(loopApi.streamLoopEvents).not.toHaveBeenCalled();
  });

  it("7. stream onError is non-fatal and does not finalize", async () => {
    seedHistory([]);
    render(<OperationHistory projectId="p1" activeLoopId="loop_e" />);
    await act(async () => {}); // flush mount-time history fetch
    await act(async () => { subs[0].cb.onError(new Error("SSE dropped")); });
    expect(screen.getByTestId("operation-history-root")).toBeInTheDocument();
    expect(screen.queryByTestId(/op-history-row-collapsed/)).toBeNull();
    // Not marked handled — a genuine later terminal event still finalizes.
    await act(async () => {
      subs[0].cb.onEvent(rbEvent("completed", 3, "Rollback complete"));
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.getByTestId("op-history-row-collapsed-rollback-loop_e"))
        .toBeInTheDocument();
    });
  });
});
