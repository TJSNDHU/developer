/**
 * PersistentFixBar.test.jsx — Iter 303 (Frontend QA Charter Layer 1 audit — closing)
 *
 * State-sync behavior tests for the always-visible bulk-fix bar.
 * Same 3-test template. Uses the exported `__FixJobContext` to
 * inject a stub job state — avoids the real EventSource-owning
 * provider from FixJobContext.jsx.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import PersistentFixBar from "../PersistentFixBar.jsx";
import { __FixJobContext } from "../FixJobContext.jsx";


// Build a stub context value with SANE defaults + overrides.
function jobCtx(overrides = {}) {
  return {
    // identity
    jobId: "job-abc", total: 5,
    // derived
    status: "running", terminal: null, error: null,
    hydrated: false, canRestart: false,
    completed: 2, failed: 0, remaining: 3,
    allRows: [], completedRows: [],
    activeRow: { file: "backend/routers/health.py" },
    // timing
    startedAt: 0, endedAt: null, lastEventAt: 0, eventCount: 0,
    // UI
    panelVisible: false, dismissed: false,
    showPanel: vi.fn(), hidePanel: vi.fn(), togglePanel: vi.fn(),
    startJob: vi.fn(), dismiss: vi.fn(),
    cancel:   vi.fn(), restart: vi.fn(),
    restarting: false, items: {},
    ...overrides,
  };
}

function Wrap({ ctx, children }) {
  return (
    <__FixJobContext.Provider value={ctx}>
      {children}
    </__FixJobContext.Provider>
  );
}


describe("PersistentFixBar — state-sync behavior (iter303)", () => {
  it("reaches-correct-terminal-state: status='done' flips label + data-status; dismiss reveals X button", () => {
    const ctx = jobCtx({ status: "running", completed: 2, failed: 0, remaining: 3 });
    const { rerender } = render(<Wrap ctx={ctx}><PersistentFixBar /></Wrap>);
    const bar = screen.getByTestId("persistent-fix-bar");
    expect(bar.getAttribute("data-status")).toBe("running");
    expect(screen.getByTestId("persistent-fix-bar-label").textContent).toBe("Fixing codebase");
    // Running → NO dismiss X button visible.
    expect(screen.queryByTestId("persistent-fix-bar-dismiss")).toBeNull();

    // Terminal event: done. Label + data-status MUST flip in the SAME
    // render; dismiss X appears.
    const doneCtx = jobCtx({
      status: "done", completed: 5, failed: 0, remaining: 0,
      terminal: { phase: "done", ok: true },
    });
    rerender(<Wrap ctx={doneCtx}><PersistentFixBar /></Wrap>);
    const bar2 = screen.getByTestId("persistent-fix-bar");
    expect(bar2.getAttribute("data-status")).toBe("done");
    expect(screen.getByTestId("persistent-fix-bar-label").textContent).toBe("Fix complete");
    // Badge now says "5 done" (completed - failed = 5).
    expect(screen.getByTestId("persistent-fix-bar-badge").textContent).toBe("5 done");
    expect(screen.getByTestId("persistent-fix-bar-dismiss")).toBeInTheDocument();
  });

  it("clears-stale-prior-state: dismissed=true unmounts the bar entirely (no stale 'Fix complete' banner)", () => {
    const { rerender, container } = render(
      <Wrap ctx={jobCtx({ status: "done", terminal: { phase: "done" }, completed: 5 })}>
        <PersistentFixBar />
      </Wrap>
    );
    expect(screen.getByTestId("persistent-fix-bar")).toBeInTheDocument();

    // User clicked X → dismiss(). The provider sets dismissed=true.
    // The bar MUST unmount in the SAME render — otherwise the "Fix
    // complete" banner persists across route changes (the exact
    // charter bug class this suite guards).
    rerender(
      <Wrap ctx={jobCtx({ status: "done", dismissed: true })}>
        <PersistentFixBar />
      </Wrap>
    );
    expect(screen.queryByTestId("persistent-fix-bar")).toBeNull();
    expect(screen.queryByText(/fix complete/i)).toBeNull();
    expect(container.firstChild).toBeNull();
  });

  it("race-condition: status='idle' returns null even with loud residual props (dismiss button, terminal, error)", () => {
    // Guards against a future OR-fallback refactor that would render
    // the bar based on ANY truthy job field. Idle is the sole gate.
    const { container } = render(
      <Wrap ctx={jobCtx({
        status: "idle", terminal: { phase: "done" },
        completed: 99, failed: 3, remaining: 0,
        error: "stale error", activeRow: { file: "loud.py" },
      })}>
        <PersistentFixBar />
      </Wrap>
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("persistent-fix-bar")).toBeNull();
    expect(screen.queryByText(/loud\.py/)).toBeNull();
  });

  it("toggle button fires togglePanel EXCLUSIVELY — never dismiss (no cross-wiring)", () => {
    const togglePanel = vi.fn();
    const dismiss     = vi.fn();
    render(
      <Wrap ctx={jobCtx({ status: "running", togglePanel, dismiss })}>
        <PersistentFixBar />
      </Wrap>
    );
    fireEvent.click(screen.getByTestId("persistent-fix-bar-toggle"));
    expect(togglePanel).toHaveBeenCalledTimes(1);
    expect(dismiss).not.toHaveBeenCalled();
  });
});
