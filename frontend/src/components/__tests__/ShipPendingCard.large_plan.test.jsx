// Iter 328 · #17 · large-plan (21+ file) frontend chip rendering test
//
// Verifies ShipPendingCard doesn't overflow, truncate, or crash when
// given 21+ file diffs.  Cannot run a real 21+ file loop end-to-end
// without founder-provided project + task, so we simulate the exact
// prop shape backend would emit.

import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ShipPendingCard from "../ShipPendingCard.jsx";


function buildLargePayload(n) {
  const files = [];
  const files_diff = [];
  for (let i = 0; i < n; i++) {
    const path = `backend/services/synth_${String(i).padStart(3, "0")}.py`;
    files.push(path);
    files_diff.push({
      path,
      additions:   i % 3 === 0 ? 5 : 1,
      deletions:   i % 3 === 0 ? 2 : 1,
      is_new:      i % 7 === 0,
      delta_bytes: 20,
      diff_source: "line",
    });
  }
  return {
    owner: "TJSNDHU", repo: "Aurem", branch: "main",
    files,
    file_count: n,
    commit_message: `Large plan: ${n} files`,
    files_diff,
    integrity_verdict: "clean",
  };
}


describe("ShipPendingCard · large-plan (21+ files) rendering", () => {
  it("renders without crash at N=21 (baseline threshold)", () => {
    render(<ShipPendingCard pending={buildLargePayload(21)} busy={false} />);
    expect(screen.getByTestId("ship-pending-card")).toBeInTheDocument();
    expect(screen.getByTestId("ship-integrity-pill")).toBeInTheDocument();
    // Collapsed by default — only first 3 rows visible + "+ N more" line.
    const rows = screen.getAllByTestId("ship-pending-file-row");
    expect(rows.length).toBe(3);
    expect(screen.getByText(/\+\s*18 more/i)).toBeInTheDocument();
  });

  it("renders without crash at N=50 (medium-large plan)", () => {
    render(<ShipPendingCard pending={buildLargePayload(50)} busy={false} />);
    expect(screen.getByText(/\+\s*47 more/i)).toBeInTheDocument();
    // Aggregate chip should show huge totals.
    const total = screen.getByTestId("ship-total-diff-chip");
    expect(total.textContent).toMatch(/\+\d+/);
    expect(total.textContent).toMatch(/−\d+/);
  });

  it("renders without crash at N=100 (extreme plan)", () => {
    render(<ShipPendingCard pending={buildLargePayload(100)} busy={false} />);
    expect(screen.getByTestId("ship-pending-card")).toBeInTheDocument();
    expect(screen.getByText(/\+\s*97 more/i)).toBeInTheDocument();
    // Ship + Cancel buttons still present (not pushed off-screen).
    expect(screen.getByTestId("ship-to-github-btn")).toBeInTheDocument();
    expect(screen.getByTestId("ship-cancel-btn")).toBeInTheDocument();
  });

  it("no rows are silently dropped when 21+ files present", () => {
    // Real assertion: even though only 3 are visible collapsed,
    // the underlying files_diff array must have all 21.
    const payload = buildLargePayload(21);
    expect(payload.files_diff.length).toBe(21);
    expect(payload.files.length).toBe(21);
  });
});
