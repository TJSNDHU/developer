/**
 * playwright.config.js — Frontend QA Charter Layer 2 (iter 299)
 *
 * Visual regression suite. Runs against the live preview app on
 * port 3000 (started by supervisor / `yarn start`). Chromium only —
 * we don't ship the full 3-browser matrix in CI to keep the
 * baseline set small and the run time under 90 s.
 *
 * Conventions:
 *   - Baselines live next to each test in `__screenshots__/<test-file>/`.
 *   - Pixel diff threshold: 0.02 (2%) — tuned to catch layout /
 *     colour drift while tolerating anti-aliasing noise.
 *   - Fonts + animations are frozen via CSS on every navigate so a
 *     spinner mid-frame doesn't cause a false positive.
 *   - Update baselines with `npx playwright test --update-snapshots`
 *     when a UI change is INTENTIONAL. See docs/visual_regression.md.
 */
import { defineConfig, devices } from "@playwright/test";

const PREVIEW_URL =
  process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3000";

export default defineConfig({
  testDir: "./tests/visual",
  fullyParallel: false,          // Sequential — the preview app is a
                                 // single shared instance, parallel
                                 // navigation causes flaky captures.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  timeout: 30_000,
  expect: {
    // 2% pixel diff tolerance — tight enough to catch a padding
    // change, loose enough to survive font hinting differences
    // between local dev machines and CI runners.
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.02,
      animations: "disabled",
    },
  },
  use: {
    baseURL: PREVIEW_URL,
    viewport: { width: 1440, height: 900 },
    // Freeze anything that could shift between runs.
    colorScheme: "light",
    locale: "en-US",
    timezoneId: "UTC",
    // Ignore HTTPS errors for the preview cert.
    ignoreHTTPSErrors: true,
    // Trace + video on failure only — keeps CI artifacts small.
    trace: "retain-on-failure",
    video: "off",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium-desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
});
