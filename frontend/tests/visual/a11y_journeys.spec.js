/**
 * tests/visual/a11y_journeys.spec.js — Iter 302 (Frontend QA Charter Layer 3 — C3)
 *
 * axe-core against fully-rendered pages via @axe-core/playwright.
 * Complements the vitest-axe component-level suite (C2) by catching
 * a11y issues that only appear when the whole page is composed —
 * focus order, live-region announcements, landmark structure, dynamic
 * ARIA on interactive widgets.
 *
 * Charter Layer 3 target routes:
 *   • Login form (auth surface)
 *   • Landing (marketing / SEO surface)
 *   • Loop live feed demo (LoopLiveFeed + growing bubble — the exact
 *     "dynamic content screen readers mishandle" class the charter
 *     called out)
 *
 * Discipline: baselined via `docs/a11y_journey_baseline.json` — new
 * violation IDs fail CI, known-existing ones don't (burn-down).
 * Same pattern as the vitest-axe component suite.
 */
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync, existsSync } from "fs";
import path from "path";


// `process.cwd()` inside `npx playwright test` is the frontend/ dir.
// Baseline is committed at repo-root/docs/a11y_journey_baseline.json.
const BASELINE_PATH = path.resolve(
  process.cwd(), "..", "docs", "a11y_journey_baseline.json"
);


function loadBaseline() {
  if (!existsSync(BASELINE_PATH)) return {};
  try { return JSON.parse(readFileSync(BASELINE_PATH, "utf8")); }
  catch { return {}; }
}


async function scanAndAssertNoNewViolations(page, journeyKey) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const actualIds = results.violations.map((v) => v.id);
  const known = new Set((loadBaseline()[journeyKey]) || []);
  const newOnes = actualIds.filter((id) => !known.has(id));
  expect(
    newOnes,
    `${journeyKey}: NEW a11y violations (not in baseline): ${newOnes.join(", ")}. ` +
    `Either fix the component OR (with reviewer sign-off) add these ` +
    `rule ids to docs/a11y_journey_baseline.json["${journeyKey}"].`
  ).toEqual([]);
}


test.describe("a11y journeys — axe-core E2E (iter 302, charter L3 C3)", () => {
  test("Login page — WCAG 2.2 A + AA, no new violations", async ({ page }) => {
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    await scanAndAssertNoNewViolations(page, "login");
  });

  test("Landing page — WCAG 2.2 A + AA, no new violations", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    await scanAndAssertNoNewViolations(page, "landing");
  });

  test("Loop live feed demo — dynamic-content a11y, no new violations",
        async ({ page }) => {
    await page.goto("/dev/loop-live-feed", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    await scanAndAssertNoNewViolations(page, "loop-live-feed");
  });
});
