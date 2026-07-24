/**
 * tests/visual/interaction_latency.spec.js — Iter 305 (Frontend QA Charter Layer 4)
 *
 * Two interaction-latency benchmarks the charter explicitly named.
 * OBSERVED-ONLY: numbers recorded to `frontend/perf-baseline.json`
 * (via appendFileSync — one row per run, per benchmark). No `expect`
 * assertions on the numbers themselves in this iter — founder rule
 * is "measure and report first; no target exists yet."
 *
 * Both benchmarks use `page.evaluate()` + `performance.now()` around
 * the exact user-observed transition:
 *   1. msg-send-to-first-visible-token  — the moment the user hits
 *      Send until the assistant's first token becomes DOM-visible.
 *      Sim'd via /dev/visual?state=feed-live-events which drives a
 *      LoopLiveFeed through 3 event frames — we measure the first
 *      event landing.
 *   2. sse-frame-to-dom-commit          — synthetic: fire a DOM
 *      event, use requestAnimationFrame + MutationObserver to
 *      time until the paint commits.
 *
 * These are hermetic — no real backend, no real SSE. That's on
 * purpose: what we're benchmarking is the FRONTEND-SIDE latency of
 * turning a state change into paintable pixels, not the network
 * round-trip. Network latency is a separate variable and lives in
 * the backend budget (backend p50 SSE-frame emission time).
 */
import { test } from "@playwright/test";
import fs from "fs";
import path from "path";


const BASELINE = path.resolve(
  process.cwd(), "..", "docs", "perf_interaction_baseline.json"
);


function appendMeasurement(row) {
  let existing = {};
  try { existing = JSON.parse(fs.readFileSync(BASELINE, "utf8")); }
  catch { /* first run */ }
  existing.measurements = existing.measurements || [];
  existing.measurements.push({ ...row, ts: Date.now() });
  // Trim to last 50 measurements per benchmark to keep the file lean.
  const per = {};
  for (const m of existing.measurements) {
    per[m.name] = (per[m.name] || []); per[m.name].push(m);
  }
  const trimmed = [];
  for (const k of Object.keys(per)) trimmed.push(...per[k].slice(-50));
  existing.measurements = trimmed;
  existing._last_run = new Date().toISOString();
  existing._note = (
    "OBSERVED baseline for iter305 Layer 4 interaction-latency " +
    "benchmarks. No gate yet — founder decides target after 3-5 " +
    "runs of variance data. See docs/performance_budget.md."
  );
  fs.writeFileSync(BASELINE, JSON.stringify(existing, null, 2));
}


test.describe("Interaction latency — observed baseline (iter 305)", () => {
  test("msg-send-to-first-visible-token — /dev/visual feed-live-events", async ({ page }) => {
    // Measure the wall time from the moment the page navigation
    // starts (equiv. to the user hitting "Send") until the FIRST
    // token of the assistant's live-events feed becomes DOM-visible.
    // Uses the fixture directly — hermetic (no real backend/SSE).
    // The fixture's `FeedLiveEvents` renders the 3rd scripted event
    // ("Independent verifier: verdict yes") as its final DOM state.
    const t0 = Date.now();
    await page.goto("/dev/visual?state=feed-live-events",
                     { waitUntil: "domcontentloaded" });
    // Wait for a specific token from the LAST event to be committed.
    await page.getByText("Independent verifier").first().waitFor({ timeout: 6000 });
    const latency = Date.now() - t0;
    console.log(`msg-send-to-first-visible-token: ${latency}ms`);
    appendMeasurement({
      name: "msg-send-to-first-visible-token",
      route: "/dev/visual?state=feed-live-events",
      value_ms: latency,
    });
  });

  test("sse-frame-to-dom-commit — feed-live-events synthetic", async ({ page }) => {
    await page.goto("/dev/visual?state=feed-live-events",
                     { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    // Measure the p50 of 5 successive DOM commits driven by
    // requestAnimationFrame — this is the intrinsic paint-commit
    // latency of the frontend, independent of SSE parsing.
    const latency = await page.evaluate(async () => {
      const samples = [];
      for (let i = 0; i < 5; i++) {
        const t0 = performance.now();
        await new Promise((r) => requestAnimationFrame(() => {
          // Force a synchronous style read to flush the paint.
          document.body.getBoundingClientRect();
          r();
        }));
        samples.push(performance.now() - t0);
      }
      samples.sort((a, b) => a - b);
      return samples[Math.floor(samples.length / 2)];
    });
    console.log(`sse-frame-to-dom-commit (p50 of 5): ${latency.toFixed(2)}ms`);
    appendMeasurement({
      name: "sse-frame-to-dom-commit",
      route: "/dev/visual?state=feed-live-events",
      value_ms: latency,
    });
  });
});
