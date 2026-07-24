# Performance Budget (Frontend QA Charter — Layer 4)

Iter 305. Owns the pixel-accurate load-time truth of AUREM's
customer-facing surfaces so a bundle-size regression, a stale
render-blocking script, or a layout-shifting hero image fails
CI instead of shipping.

---

## The gate — Google's "Good" Core Web Vitals

Founder-approved (iter 305). Non-negotiable ceilings on the
production build:

| Metric                          | Budget    | Why this number                                                                    |
| ------------------------------- | --------- | ---------------------------------------------------------------------------------- |
| Largest Contentful Paint (LCP)  | ≤ 2500 ms | Google's "Good" threshold — the p75 target across all real-world CrUX data. Above 4s = "Poor" and directly hurts SEO ranking. |
| Total Blocking Time (TBT)       | ≤ 200 ms  | Google's "Good" threshold — proxy for INP-of-old / p75 first-input-delay ceiling.  |
| Cumulative Layout Shift (CLS)   | ≤ 0.1     | Google's "Good" threshold — matches CrUX's p75 field-lab boundary.                 |

Reference: [Core Web Vitals thresholds](https://web.dev/articles/vitals).

---

## Routes measured

`.lighthouserc.json` runs Lighthouse against 3 unauthenticated
routes on the production build (`yarn preview`, port 4173):

| Route                       | Reason to gate                             |
| --------------------------- | ------------------------------------------ |
| `/`                         | Marketing / SEO entry point                |
| `/login`                    | Every returning user hits this             |
| `/dev/loop-live-feed`       | The component demo — pre-flight for chat  |

Auth-gated routes (`/dashboard`, `/build/*`) are deferred to
Batch 2 — same reason as visual regression: need seeded session
fixture, out of iter 305 scope.

---

## Actual numbers on the first passing run (iter 305)

Median of 2 Lighthouse runs each, chromium headless, desktop
preset, run inside `mcr.microsoft.com/playwright:v1.61.1-jammy`
against the production build:

| Route                       | LCP       | TBT     | CLS      |
| --------------------------- | --------- | ------- | -------- |
| `/`                         | 1147 ms   | 6 ms    | 0.0125   |
| `/login`                    | 1078 ms   | 0 ms    | 0.0008   |
| `/dev/loop-live-feed`       |  848 ms   | 0 ms    | 0.0093   |

All three routes clear every budget with substantial margin
(> 1.3 s of LCP headroom, > 190 ms TBT headroom, > 0.08 CLS
headroom).

---

## Diagnostic finding — the dev-server vs prod-build trap

First `.lighthouserc.json` (removed) ran against the **dev
server** (`yarn start` on port 3000). LCP registered
**4775-4854 ms across all 3 routes** — a hard failure of the
2500 ms budget. Root cause: Vite's dev server serves ES modules
un-bundled with a waterfall of `import` requests, which inflates
LCP roughly 4x versus the production bundle.

**Discipline**: Lighthouse CI MUST run against `vite preview`
(prod build), NEVER `yarn dev`. Also true for competitors — the
industry-standard `startServerCommand` in `.lighthouserc.json`
always spawns the preview server, never the dev server.

**Do NOT lower the budget to accommodate the dev-server number** —
that's the exact silent-lowering the founder rule forbids.
Instead, measure against `vite preview` where the number is
honest, and gate on that.

---

## Interaction-latency benchmarks (observed only — not yet gated)

Two additional benchmarks from `frontend/tests/visual/
interaction_latency.spec.js`. **No gate exists yet** — founder
rule (iter 305) is "measure and report first; no target."
Numbers append to `docs/perf_interaction_baseline.json` on each
run so we can see variance over time.

| Benchmark                        | First observed | What it measures                                                        |
| -------------------------------- | -------------- | ----------------------------------------------------------------------- |
| `msg-send-to-first-visible-token` | 438 ms         | Wall time from navigation start (proxy for "user hit Send") until the FIRST assistant token becomes DOM-visible. Uses the `/dev/visual?state=feed-live-events` fixture — hermetic (no real backend). |
| `sse-frame-to-dom-commit`         |  16.7 ms       | p50 of 5 successive `requestAnimationFrame` commits with a forced style-read flush. Intrinsic paint-commit latency of the frontend, independent of SSE parsing / network. |

After 3-5 CI runs land, we'll have variance data and can decide
a real target. Until then: **observe only**, no failing.

---

## Local workflow

Run Lighthouse locally (builds + previews internally):

    cd frontend
    npx lhci autorun --config=.lighthouserc.json

Open the report:

    npx lhci open

Run interaction-latency benchmarks:

    cd frontend
    npx playwright test tests/visual/interaction_latency.spec.js

The measurements append to `docs/perf_interaction_baseline.json`
(last-50-per-benchmark, trimmed).

---

## What's deliberately deferred (charter L4 Batch 2)

- Auth-gated route budgets — needs seeded session fixture.
- Real-device (mobile) preset — desktop is the first gate; mobile
  budgets are typically stricter (LCP ≤ 4s "Good" for slower
  connections).
- INP (Interaction to Next Paint) — new CWV metric replacing FID
  from March 2024, but Lighthouse lab measurement is not yet
  stable enough to gate on. Watch [web.dev/articles/inp](https://web.dev/articles/inp) for tooling maturity.
- The two interaction-latency benchmarks becoming HARD GATES —
  needs 3-5 runs of variance data + founder-approved target.

---

## Environment parity note

Lighthouse numbers on the local dev machine can differ from CI
runners due to CPU throttling assumptions + noisy-neighbour VMs.
Google's "Good" thresholds are chosen to be robust to that
variance — that's why we picked THEM as the gate instead of
inventing our own. If CI-only failures appear at (say) LCP =
2600 ms, the fix is a code change, not raising the budget.

See `docs/environments.md` for the full environment-parity ledger.
