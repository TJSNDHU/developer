// frontend/src/lib/__tests__/shipPending_wire_to_render.integration.test.jsx
//
// Iter 328 · hotfix v3 — the "component tests passed but the real
// app dropped the fields" gap-closer.
//
// ShipPendingCard.test.jsx passes because it mounts with a HAND-BUILT
// pending object.  shipPendingMappers.test.js passes because it runs
// the mapper on the RAW wire shape.  But neither test proves the
// CHAIN: raw wire shape → mapper → ShipPendingCard renders pill +
// chips.  That's the exact gap the founder's prod fiber trace hit
// three times.
//
// This integration test closes it by chaining the two units end-to-
// end: take the raw wire shape /loop/active actually returns, run
// it through mapShipPendingFromActive, then mount ShipPendingCard
// with the mapper output, and assert the pill + total chip + per-
// file chip actually appear in the DOM.  If any mapper key drops
// files_diff or integrity_verdict, this test red-lights instantly.

import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import {
  mapShipPendingFromActive,
  mapShipPendingFromAwaitingShipEvent,
} from "../shipPendingMappers.js";
import ShipPendingCard from "../../components/ShipPendingCard.jsx";


// The exact wire shape /loop/active returned on the failing eyeball
// (founder-verified via prod fiber inspection).  This is the ground
// truth — do not mutate.
const REAL_LOOP_ACTIVE_SHIP_PENDING = {
  owner: "TJSNDHU",
  repo: "Aurem",
  branch: "main",
  files: { "AUDIT.md": "# Audit\n\nContent." },
  commit_message: "Add AUDIT.md",
  files_diff: [
    {
      path: "AUDIT.md",
      additions: 1, deletions: 1,
      is_new: false, delta_bytes: 0, diff_source: "line",
    },
  ],
  integrity_verdict: "clean",
};

const REAL_SSE_AWAITING_SHIP = {
  kind: "awaiting_ship",
  owner: "TJSNDHU",
  repo: "Aurem",
  branch: "main",
  files: ["AUDIT.md"],
  file_count: 1,
  commit_message: "Add AUDIT.md",
  files_diff: [
    {
      path: "AUDIT.md",
      additions: 35, deletions: 34,
      is_new: false, delta_bytes: 18, diff_source: "line",
    },
  ],
  integrity_verdict: "clean",
};


describe("wire → mapper → ShipPendingCard render (integration)", () => {
  it("/loop/active rehydrate path: pill + per-file chip render end-to-end", () => {
    const pending = mapShipPendingFromActive(REAL_LOOP_ACTIVE_SHIP_PENDING);
    // Sanity — mapper must have carried the fields through.
    expect(pending.files_diff.length).toBe(1);
    expect(pending.integrity_verdict).toBe("clean");
    // Now mount the REAL component with that exact object.
    render(<ShipPendingCard pending={pending} busy={false} />);

    const pill = screen.getByTestId("ship-integrity-pill");
    expect(pill).toBeInTheDocument();
    expect(pill).toHaveAttribute("data-verdict", "clean");

    const row = screen.getByTestId("ship-pending-file-row");
    expect(row).toHaveAttribute("data-file-path", "AUDIT.md");
    const chip = within(row).getByTestId("ship-file-diff-chip");
    expect(chip).toHaveAttribute("data-diff-source", "line");
    expect(chip).toHaveTextContent("+1");
    expect(chip).toHaveTextContent("−1");
  });

  it("SSE awaiting_ship path: same E2E chain, different wire shape", () => {
    const pending = mapShipPendingFromAwaitingShipEvent(
      REAL_SSE_AWAITING_SHIP, { message: "Ready to ship." });
    expect(pending.files_diff.length).toBe(1);
    expect(pending.integrity_verdict).toBe("clean");
    render(<ShipPendingCard pending={pending} busy={false} />);

    expect(screen.getByTestId("ship-integrity-pill"))
      .toHaveAttribute("data-verdict", "clean");
    expect(screen.getByTestId("ship-total-diff-chip"))
      .toHaveTextContent(/\+35.*−34/);
  });

  it("regression guard — if either mapper drops files_diff, pill is absent", () => {
    // Simulate what happens if someone reverts the mapper to the old
    // whitelist that drops files_diff (this is the failure mode the
    // founder actually hit).  We do NOT change the mapper — we
    // manually build a "stripped" pending to prove the render
    // gracefully degrades AND the pill is correctly absent so the
    // test can assert this is the DROP-fields shape.
    const stripped = mapShipPendingFromActive(REAL_LOOP_ACTIVE_SHIP_PENDING);
    delete stripped.files_diff;
    delete stripped.integrity_verdict;
    render(<ShipPendingCard pending={stripped} busy={false} />);
    // Pill absent (regression) — this is what founder saw.
    expect(screen.queryByTestId("ship-integrity-pill")).toBeNull();
    expect(screen.queryByTestId("ship-file-diff-chip")).toBeNull();
    // But the base card still renders (fail-open).
    expect(screen.getByTestId("ship-pending-card")).toBeInTheDocument();
  });
});
