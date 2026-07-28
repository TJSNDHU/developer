# AUREM CTO — Product Requirements Document (living)

**Last updated**: 2026-07-27 (Iter 329 · Task 2 Deploy A-Recovery)
**Live**: https://auremcto.com

## Original problem statement
Optimize onboarding, strict separation of backend founder logic, and expand codebase health scanners.
Build the "Personal Track" for non-technical users, implement the "Master QA Test Strategy", fix
Loop engine stuck bugs (Phase 0), then build the next 4 competitive differentiators:
- Phase 1: Persistent Correction Rules
- Phase 2: Risk-Based Routing
- Phase 3: Checkpoints/Rollback
- Phase 4: Browser Self-Testing

Language: **Hinglish** — main agent responds in Hinglish.

## What's implemented (chronological, most recent first)

### Iter 329 · Task 2 Deploy A-Recovery (2026-07-27) — Rollback UI hardening
- **Fix A** — Dropped redundant `terminal` gate on `ShippedRow` render (`LoopLiveFeed.jsx`). Server-side invariant (`loop_engine.py:2823-2944`) proves `commit_sha` is never optimistically set; `shipInfo` alone is a sufficient terminal signal. Eliminates Bug X unmount race that destroyed phase state between clicks.
- **Fix B** — `LoopStatusChip` terminal grace split by outcome: success persists indefinitely until Done click, failure keeps 30s auto-dismiss.
- **Fix C** — Rollback confirm-click hardening: `phaseRef` mirror replaces stale-closure phase capture, confirm window 4s→10s, high-contrast red-fill/white-text button + distinct testid variant, REAL-TIMER two-click test added (real setTimeout + 1.5s wait — the missing test class).
- **187/187 frontend tests pass** — production deploy dispatched.

### Iter 329 · Task 2 Deploy A (2026-07-27) — inline rollback UI (superseded by A-Recovery)
- Loop-mode ship modal suppressed (`ChatPanel.jsx` `kind:"shipped"` dispatch removed)
- Inline "Shipped {sha7} · View on GitHub · Rollback" row + "Done" chip button
- `rollbackLoop()` helper wired to production-proven POST `/loop/{id}/rollback`

### Iter 329 · Deploy 3-A (2026-07-27) — rollback route production-verified
- Founder ran real ship (5d939a4) → curl'd POST /rollback with confirm="ROLLBACK" → real GitHub revert commit (ea3ebcf) landed with "chore: revert 5d939a4 [via ORA]", non-force-push, parent preserved. Backend rollback service fully proven.

### Iter 329 · Fix A/B/C prior (2026-07-27) — render-layer resilience
- **Fix A (data-seeding)**: `init_prod_collections.py` seed rewritten from `insert_many(if empty)` → per-flag `$setOnInsert` upsert. `integration_health_cron` flag now propagates to production. **Production-verified.**
- **Fix B (LoopLiveFeed pending resolver)**: `resolveTerminalTone` + `resolvePendingOnTerminal` — pending narration lines auto-flip to success/warning/danger on terminal state. **Production-verified via commit 1f70444 real ship.**
- **Fix C (LoopStepBar + LoopStatusChip terminal resolution)**: Rule 0 extended in `ecgVariant`; `phaseText` prefers terminal state over mid-loop phase. **Production-verified via commit d372b92 real ship.**

### Iter 328 (2026-07-27) — Multi-item bundle
- Deploy 2 hotfix v3: extracted `shipPendingMappers.js` (single source of truth for ship state shape). Founder-verified via real ship.
- `/feature-window` auth gate (401→login, 403→dashboard, founder→system map)
- Periodic `integration_health_cron` (600s, env-gated)
- SYSTEM_INVENTORY auto-append wired to `_bg_bootstrap`
- ShipPendingCard enrichment (Integrity guard pill + per-file diff chips)
- Chat-history B3 fix approved but NOT YET SHIPPED — write cap `$slice: -40` → `-200` (queued in Deploy B)
- Chat-history B1-race fix approved but NOT YET SHIPPED — Shell.jsx session-mint deferral with 3s fallback timeout (queued in Deploy B)

## Prioritized backlog (top of queue → bottom)

**P0 — immediately after founder verifies Deploy A-Recovery:**
- **Bug 1** — PLAN step never turns green during EXECUTE. Root cause: backend never emits `step="plan"` narration (confirmed via enum-scan). Fix: either emit plan-success narration on approval, or extend frontend legacy fallback for PLAN specifically.
- **Bug 2 / #6** — Iter 309 ECG pulse-wave animation not visible on production. Founder confirmed via SVG scan. Verify deploy status; if not shipped, deploy standalone.

**P1 — after Bugs 1+2:**
- **Deploy B** — Chat-history B3 (`chat.py` `$slice: -200`) + B1-race (`Shell.jsx` mint deferral with 3s fallback). Zero file overlap with Deploy A-Recovery.
- **#3 · ORA learning resurrection** (a→e): fail-open logging → callsite reattach → real Mongo before/after proof → canary/eval flags ON (shadow) → learning-health tile on `/admin/architecture`.
- **Task 1 · ORA Canary + Admin Health Tile** — `ORA_CANARY_ENABLED` + `ENABLE_EVAL_CRON`, tile reads `project_brains.max(updated_at)`, RED if >24h stale.
- **#14 · dead-code delete list execution** — tool_executor.py (already gone), tools_bridge.py (still on disk), DevVisual/DevLoopLiveFeed (never existed under those names — need founder to confirm the correct filenames).
- **#17 · Large-plan E2E** — real live loop with 21+ files through PLAN→EXECUTE→SCAN→integrity guard→ship gate.

**P2 (batch when free):**
- #12 Tier 3 discrepancy (Loop Readiness Score, pattern templates, branch-per-fix, trust levels)
- #13 Surface MCP / ShipWall / Referral features
- #15 ORA skills usage prune proposal
- #16 Speed Diagnostic — `plan_latency_profile` data pull

**Founder-only (external decisions):**
- Stripe Dashboard webhook config check
- Tavily key top-up/rotate
- Firecrawl prod-secret confirmation
- `DashboardPreviewV2.jsx` launch-vs-delete decision
- Phase 1-4 design scope confirmations
- CISO landing page (post customer-discovery)

## Standing rules (enforced this session)
- Every "done"/"verified" claim MUST include actual raw output (API JSON, Mongo query, DOM snapshot) inline in the report — not just prose.
- Test-first for every fix. Real-timer / real-Mongo / real-live tests when possible; not just synthetic mocks.
- Separate deploys for different classes of change (data-seeding vs UI vs backend logic).
- No blind re-dispatch on cache lag — real functional verification is source of truth.
- Founder verifies production via real ship + real click + real DOM/GitHub check.

### Iter 327 · Firecrawl diagnostic prod logging
### Iter 326 · Tavily 432 warn reclassification + Stripe .recurring validation
### Iter 325 · Ship completion UI + failed chip repositioning
### Iter 324 · ChatPanel duplicate bubble suppression + F12 chip repositioning
### Iter 323 · Ship-completion UI polish
### Iter 322 · Plan-phase latency profiling telemetry
### Iter 321 · Removed recurring console.clear()
### Iter 320 · Reload rehydration + LoopStepper sync
### Iter 319 · Scan-phase NameError fixed + fail-CLOSED
### Iter 318 · loop_integrity_guard.py — data-loss prevention pre-ship
### Iter 309 · SSE reconnect harness (PASSED 25-min test)

## Prioritized backlog / P0-P2 remaining

### P0 (needs founder input to unblock)
- **#2 Iter 329 frontend + real revert proof** — HELD on end-to-end proof
- **#3 ORA shadow-learning resurrection** — HELD, requires timestamp proof
- **#7 Stripe webhook dashboard verify** — founder must check dashboard endpoint state
- **#8 Tavily key top-up** — founder-dependent
- **#9 Firecrawl prod secret confirm** — founder-dependent
- **#14 dead-code delete approvals** — 4 files await founder yes/no
- **#10 sweepers dry-run approval** — awaiting founder yes/no
- **#11 feature-flag first consumer** — awaiting founder proposal

### P1 (queued but not started this session)
- **#6 Iter 309 ECG/narration standalone deploy** — feature-parity check first
- **#13 MCP/ShipWall/Referral surface** — one at a time with approval
- **#17 large-plan (21+ file) edge case test**
- **#18-21 Phase 1-4** — design confirmation required first

### P2 (analysis-only, DONE this session)
- ✅ #12 Tier 3 discrepancy (report in SYSTEM_INVENTORY.md)
- ✅ #14 dead-code report (list documented)
- ✅ #15 skills usage report (documented in session summary)
- ✅ #16 plan-latency report (data absent — dead write path)

## Key architecture invariants
- ObjectId serialization: `PyObjectId` / `BaseDocument` pattern
- datetime: `datetime.now(timezone.utc)`
- Ripple-Update Rule: any change triggers same-iteration SYSTEM_INVENTORY.md update via `services/inventory_service.py`
- Fail-open discipline: all background tasks (learning, inventory, cron) must never block user paths
- Trust levels enforced at L1 (block low-trust from paid features) + L3 (auto-ship gate skip)

## Test discipline
- Backend: pytest under `backend/tests/` — all iter 328 additions have tests
- Frontend: vitest + RTL under `src/**/__tests__/` and `src/**/*.test.jsx` — 124/124 passing
- **NEW HARD RULE (added Iter 328)**: for UI regressions, an integration test that chains real wire shape → mapper → real component render is required. Component-level tests alone are insufficient (the exact gap that broke Deploy 2 three times).
