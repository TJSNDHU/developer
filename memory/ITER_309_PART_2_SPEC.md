# Iter 309 Part 2 — LoopStepBar ECG-Strip Visual QA Spec

**Status:** REQUIREMENT ONLY, NOT BUILT YET.
**Gated behind:** Founder's 25-min SSE reconnect test → Iter 309 deploy authorization.
**Do NOT build now.** This file exists so whichever agent picks up Iter 309 build has the exact visual contract already written down — no re-discovery, no ambiguity.

---

## Visual contract (founder-captured 2026-07-27, screenshot marked up)

The LoopStepBar renders 5 phase labels in a row: `LOOP · PLAN · EXECUTE · VERIFY · SCAN · SHIP`. (The `LOOP` prefix is the row label, not a phase.)

**Requirement:** Each of the 5 phase labels (`PLAN`, `EXECUTE`, `VERIFY`, `SCAN`, `SHIP`) must have its own ECG-pulse strip directly beneath it — **per-label**, not one continuous strip spanning the whole row, not misaligned/offset.

**Precise geometry constraints (all MUST hold):**
1. **Width parity** — each strip's `getBoundingClientRect().width` must equal (±1px tolerance) its associated label's `getBoundingClientRect().width`.
2. **Horizontal alignment** — each strip's `getBoundingClientRect().left` must equal (±1px tolerance) its associated label's `getBoundingClientRect().left`.
3. **Fixed vertical row** — all 5 strips share the same `getBoundingClientRect().top` (±1px). No per-label vertical drift.
4. **Directly beneath** — each strip's `top` must be within a small clamped delta (spec: 4-16px) of the corresponding label's `bottom`. No large gap, no overlap.
5. **Independent animations OK** — strips can animate independently (pulse when their phase is active, dim when idle) but the geometry above must hold at every animation frame.

## Playwright test additions (write alongside existing tone/animation checks)

The existing Iter 309 test file already has assertions for ECG stroke color, pulse rate, and tone. Add:

```javascript
// Pixel-alignment assertion — one per phase.
const phases = ["plan", "execute", "verify", "scan", "ship"];
for (const ph of phases) {
  const labelBox = await page.locator(`[data-testid="loop-step-label-${ph}"]`).boundingBox();
  const stripBox = await page.locator(`[data-testid="loop-step-strip-${ph}"]`).boundingBox();
  expect(labelBox, `${ph} label must be present`).not.toBeNull();
  expect(stripBox, `${ph} strip must be present`).not.toBeNull();
  // Width parity, 1px tolerance.
  expect(Math.abs(labelBox.width - stripBox.width)).toBeLessThanOrEqual(1);
  // Horizontal alignment, 1px tolerance.
  expect(Math.abs(labelBox.x - stripBox.x)).toBeLessThanOrEqual(1);
  // Vertical proximity (strip directly beneath label).
  const gap = stripBox.y - (labelBox.y + labelBox.height);
  expect(gap).toBeGreaterThanOrEqual(4);
  expect(gap).toBeLessThanOrEqual(16);
}
// Row parity — all 5 strips share the same top edge.
const stripTops = [];
for (const ph of phases) {
  const box = await page.locator(`[data-testid="loop-step-strip-${ph}"]`).boundingBox();
  stripTops.push(box.y);
}
const minTop = Math.min(...stripTops);
const maxTop = Math.max(...stripTops);
expect(maxTop - minTop).toBeLessThanOrEqual(1);
```

## Required test-ids (add during Iter 309 build)

Per phase:
- `loop-step-label-plan`, `loop-step-label-execute`, `loop-step-label-verify`, `loop-step-label-scan`, `loop-step-label-ship`
- `loop-step-strip-plan`, `loop-step-strip-execute`, `loop-step-strip-verify`, `loop-step-strip-scan`, `loop-step-strip-ship`

The existing `LoopStepBar.jsx` may already have label test-ids — extend the pattern; do not rename existing ones without a broader audit.

## Founder-provided visual reference

Screenshot marked up on 2026-07-27 shows the current plain step-bar with the entire empty row above the composer circled in yellow — confirming the strips row must live in that empty space, one strip per phase, width-locked to its label.
