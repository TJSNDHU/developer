/**
 * tests/visual/ship_gate.spec.js — Iter 334 (Auto-QA agent, UI layer)
 *
 * DIRECT regression lock for regression-20260728-ship-gate-infinite-loop
 * (see .emergent/qa-history/regression_library.json):
 *
 *   Bug A — the ship human-review gate rendered the generic
 *   retry/skip/abort card with NO "Approve & Ship" button.
 *
 * Uses the SAME hermetic fixture surface as state_fixtures.spec.js
 * (/dev/visual?state=ship-gate, see VisualFixtures.jsx) — no SSE, no
 * auth, no backend. Functional DOM assertions, not pixel diffs.
 *
 * Bug B (skip causing infinite Execute cycle) is a state-machine
 * contract and is locked server-side in
 * backend/tests/test_iter332_ship_gate_skip.py — a UI click-through
 * of a REAL paused loop additionally needs the Section-0 sandbox
 * account and is intentionally NOT simulated here.
 */
import { test, expect } from "@playwright/test";

test.describe("ship-gate approval card (Iter 332 regression)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/dev/visual?state=ship-gate");
    await page.waitForLoadState("networkidle");
  });

  test("Approve & Ship button exists at the ship human-review gate", async ({ page }) => {
    await expect(page.getByTestId("ship-review-gate-card")).toBeVisible();
    await expect(page.getByTestId("loop-approve-ship-btn")).toBeVisible();
    await expect(page.getByTestId("loop-approve-ship-btn"))
      .toContainText("Approve & Ship");
  });

  test("Cancel ship button exists", async ({ page }) => {
    await expect(page.getByTestId("loop-cancel-ship-btn")).toBeVisible();
  });

  test("generic Skip/Retry/Abort buttons are NOT rendered at this gate", async ({ page }) => {
    await expect(page.getByTestId("loop-skip-btn")).toHaveCount(0);
    await expect(page.getByTestId("loop-retry-btn")).toHaveCount(0);
    await expect(page.getByTestId("loop-abort-btn")).toHaveCount(0);
  });

  test("touched test files are listed for the reviewer", async ({ page }) => {
    await expect(page.getByTestId("ship-review-tests-touched")).toBeVisible();
    await expect(page.getByTestId("ship-review-tests-touched"))
      .toContainText("tests/");
  });
});
