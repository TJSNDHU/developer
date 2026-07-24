/**
 * tests/visual/state_fixtures.spec.js — Iter 302 (Frontend QA Charter Layer 2 Batch 2)
 *
 * State-specific visual regression baselines. Complements the
 * public-routes spec (iter299) by locking down the 7 state
 * transitions the charter explicitly named:
 *
 *   Phase stepper (LoopStepBar): 4 states
 *     executing | completed | failed | paused_for_user
 *
 *   LoopLiveFeed: 3 states
 *     pending-placeholder | live-events | terminal
 *
 * All 7 fixtures live at `/dev/visual?state=<name>` and are
 * hermetic — no SSE, no auth, no backend. See VisualFixtures.jsx.
 *
 * Dynamic content masking:
 *   - `[data-live-clock], [data-live-timestamp]` hidden via CSS
 *     (same helper as public_routes.spec.js) so "5s ago" copy
 *     doesn't cause false diffs.
 *
 * Baseline threshold: `maxDiffPixelRatio=0.02` — inherited from
 * playwright.config.js.
 */
import { test, expect } from "@playwright/test";


async function freezePage(page) {
  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        animation-duration: 0s !important;
        animation-delay:    0s !important;
        transition-duration: 0s !important;
        transition-delay:    0s !important;
      }
      [data-live-clock], [data-live-timestamp], video {
        visibility: hidden !important;
      }
    `,
  });
  await page.evaluate(() => document.fonts?.ready);
  await page.waitForLoadState("networkidle");
}


// The 7 charter-mandated fixtures. Each drives one component into
// one terminal-state and captures a baseline. Any pixel-level
// regression (CSS typo, class-not-applied, wrong colour) fails CI.
const FIXTURES = [
  // Phase stepper — 4 states
  { state: "step-executing",         name: "phase-stepper-executing.png" },
  { state: "step-completed",         name: "phase-stepper-completed.png" },
  { state: "step-failed",            name: "phase-stepper-failed.png" },
  { state: "step-paused-for-user",   name: "phase-stepper-paused-for-user.png" },
  // LoopLiveFeed — 3 states
  { state: "feed-pending",           name: "loop-live-feed-pending.png" },
  { state: "feed-live-events",       name: "loop-live-feed-live-events.png" },
  { state: "feed-terminal",          name: "loop-live-feed-terminal.png" },
];


test.describe("State fixtures — visual regression (iter 302)", () => {
  for (const f of FIXTURES) {
    test(`fixture: ${f.state}`, async ({ page }) => {
      await page.goto(`/dev/visual?state=${f.state}`,
                       { waitUntil: "domcontentloaded" });
      await freezePage(page);
      // Confirm the fixture rendered (not the error branch).
      await expect(page.getByTestId("visual-fixture-stage"))
        .toBeVisible();
      // Snap the whole stage — one component per fixture.
      await expect(page.getByTestId("visual-fixture-stage"))
        .toHaveScreenshot(f.name, {
          maxDiffPixelRatio: 0.02,
          animations: "disabled",
        });
    });
  }
});
