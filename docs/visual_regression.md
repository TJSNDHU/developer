# Visual regression (Frontend QA Charter — Layer 2)

Introduced in iter 299. Owns the pixel-level truth of AUREM's
unauthenticated UI so silent CSS drift (a stale utility class,
a `!important` cascade collision, an unintentional shadcn
version bump) fails CI instead of shipping.

---

## What it covers today (iter 299 + iter 302)

Chromium desktop `1440×900`, **12 baselines** across 2 spec files:

### `public_routes.spec.js` — 5 unauthenticated views

| Route                       | Baseline                        |
| --------------------------- | ------------------------------- |
| `/`                         | `landing.png`                   |
| `/why-ora`                  | `why-ora.png`                   |
| `/demo`                     | `demo.png`                      |
| `/login`                    | `login.png`                     |
| `/dev/loop-live-feed`       | `loop-live-feed-demo.png`       |

### `state_fixtures.spec.js` — 7 charter-mandated state baselines (iter 302)

Charter Layer 2 explicitly demanded state-specific baselines for
the phase stepper (4 states) and LoopLiveFeed (3 states). Driven
by `/dev/visual?state=<name>` — a fixture-only route family that
renders each component with hard-coded props (no SSE, no auth,
no backend, sub-100 ms first paint).

| Component / state                       | Fixture URL                                    | Baseline                                     |
| --------------------------------------- | ---------------------------------------------- | -------------------------------------------- |
| Phase stepper — executing               | `/dev/visual?state=step-executing`             | `phase-stepper-executing.png`                |
| Phase stepper — completed               | `/dev/visual?state=step-completed`             | `phase-stepper-completed.png`                |
| Phase stepper — failed                  | `/dev/visual?state=step-failed`                | `phase-stepper-failed.png`                   |
| Phase stepper — paused_for_user         | `/dev/visual?state=step-paused-for-user`       | `phase-stepper-paused-for-user.png`          |
| LoopLiveFeed — pending-placeholder      | `/dev/visual?state=feed-pending`               | `loop-live-feed-pending.png`                 |
| LoopLiveFeed — live-events              | `/dev/visual?state=feed-live-events`           | `loop-live-feed-live-events.png`             |
| LoopLiveFeed — terminal                 | `/dev/visual?state=feed-terminal`              | `loop-live-feed-terminal.png`                |

Baselines live next to the spec at
`frontend/tests/visual/public_routes.spec.js-snapshots/`.

Pixel diff threshold: `maxDiffPixelRatio = 0.02` (2%) — set in
`frontend/playwright.config.js`. Tight enough to catch layout /
colour drift, loose enough to survive font-hinting noise.

---

## Local workflow

Start the frontend (supervisor already handles this in the pod,
otherwise `yarn start`):

    cd frontend
    yarn start          # http://localhost:3000

Run the suite:

    npx playwright test          # against http://localhost:3000

Run against a different host (preview / staging):

    PLAYWRIGHT_BASE_URL=https://your-preview.example.com \
        npx playwright test

Interactive debug:

    npx playwright test --ui

---

## Updating baselines

Only do this **after** the UI change is intentional and reviewed.
Never run `--update-snapshots` to "make the test pass" without
inspecting the diff first.

    cd frontend
    npx playwright show-report          # inspect the diff HTML
    npx playwright test --update-snapshots
    git add tests/visual/*-snapshots

Commit message convention:

    visual: rebaseline <route> after <what-changed> (iter <N>)

---

## What's deliberately deferred

Baselines are hermetic snapshots — capturing an auth-gated screen
requires a stable authenticated state, and every flaky day the
seeded session shifts is a false-positive day. These are the
Batch-2+ items:

- Auth-gated views (`/dashboard`, `/build/*`, `/settings`) — needs
  seeded session cookie fixture.
- Interaction states — hover, focus, modal-open, drawer-open.
- Multi-viewport — mobile `375×667` + tablet `768×1024`.
- Dark mode.
- Loop live view during an active run (needs SSE fixture).

---

## Environment parity

Playwright can differ pixel-for-pixel between a local Mac dev
machine and CI Linux runners due to font substitution. The
`toHaveScreenshot` threshold (2%) is sized for this — if you see
CI-only failures under 2%, they're expected variance. If you see
larger diffs, the baseline was captured on the wrong OS.

### Option 1 — Locally, in the pinned Docker image

    docker run --rm -v $(pwd)/frontend:/w -w /w \
        mcr.microsoft.com/playwright:v1.61.1-jammy \
        npx playwright test --update-snapshots

### Option 2 — Trigger the GitHub Action (recommended)

Run the `Rebaseline Visual Regression (AMD64 Linux)` workflow via
the Actions tab (or `gh workflow run`). It:

1. Boots the frontend inside the pinned Playwright Jammy image
   (AMD64 Linux — same runner as CI).
2. Runs `--update-snapshots`.
3. Commits the new PNGs back to your feature branch with an
   audit-trail message that names the exact chromium build +
   Playwright version used.

Command:

    gh workflow run "Rebaseline Visual Regression (AMD64 Linux)" \
        -f branch=my-feature-branch \
        -f reason="rebaseline after landing page hero redesign"

The workflow refuses to run on main/master (prevents accidental
production-baseline rewrites without review).

See `docs/environments.md` for the full environment-parity ledger.

---

## Layer 2 quality gate (CI)

The `.github/workflows/quality-gate.yml` job `visual-regression`
runs on every PR:

1. Boots the frontend (`yarn start &`).
2. Waits for `:3000/` to return 200.
3. `npx playwright test` — fails the job on any pixel-diff.
4. Uploads the HTML report as an artifact so reviewers can inspect
   the exact diff without pulling the branch.

Baselines committed to the repo:

    frontend/tests/visual/public_routes.spec.js-snapshots/
      landing-chromium-desktop-linux.png
      why-ora-chromium-desktop-linux.png
      demo-chromium-desktop-linux.png
      login-chromium-desktop-linux.png
      loop-live-feed-demo-chromium-desktop-linux.png
