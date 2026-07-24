/**
 * vitest.config.js — Iter 289 (Track 1 Lane A)
 *
 * Frontend coverage instrumentation. Deliberately narrow scope:
 * we run coverage against a small, curated set of pure-JS/logic
 * files (loopApi, ChatPanel helpers) — not against every JSX file
 * — because the traceability matrix's frontend surfaces are the
 * ones we care about proving. If you widen `include` later, do it
 * one file at a time so the numbers stay honest.
 */
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.js"],
    include: ["src/**/*.test.{js,jsx,ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "json-summary"],
      reportsDirectory: "coverage",
      include: [
        "src/lib/loopApi.js",
        "src/components/LoopStepBar.jsx",
        "src/components/LoopLiveFeed.jsx",
      ],
      // Never lie about coverage — leave the threshold soft here so
      // the numbers are real; the QA MCP tool inspects the raw
      // percent and reports gaps, it does NOT gate on this file.
      thresholds: undefined,
    },
  },
});
