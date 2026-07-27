# AUREM CTO — Product Requirements Document (living)

**Last updated**: 2026-07-27 (Iter 328)
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

### Iter 328 (2026-07-27) — Multi-item bundle
- **Deploy 2 hotfix v3**: extracted `shipPendingMappers.js` — single source of truth for the shipPending state shape at both ingress paths (`/loop/active` rehydrate + SSE `awaiting_ship`). Component-level test + mapper unit tests + wire→render integration test. Structurally regression-proof.
- **`/feature-window` auth gate** (Item #4): 401 anonymous → login redirect, 403 non-founder → /dashboard, founder → system map.
- **Periodic integration_health cron** (Item #5): `services/integration_health_cron.py` runs `run_all_probes()` every 600s (env-gated). Live-verified in backend logs.
- **Iter 329 rollback backend** (Item #2, HELD): `services/loop_rollback.py` + `POST /api/aurem-dev/loop/{loop_id}/rollback`. 4 tests pass. NOT deployed until real revert commit proven on GitHub.
- **SYSTEM_INVENTORY auto-append**: `services/inventory_service.py` wired to `_bg_bootstrap` — scans `HEAD~1..HEAD` every boot, appends new routers/env vars/kinds. Idempotent HTML-comment markers.
- **ShipPendingCard enrichment** (Iter 328 · Deploy 2): green "Integrity guard: clean" pill + per-file `+N −N` chips before Ship button. Uses `services/loop_ship_diff.compute_files_diff` + Iter 318's integrity guard verdict.
- **#12 Tier 3 discrepancy report**: Loop Readiness Score NOT-FOUND, Pattern templates NOT-FOUND, Branch-per-fix EXISTS-ORPHANED, L1/L2/L3 trust EXISTS-WIRED.
- **#14 dead-code purge**: 4 provably reference-free candidates identified (tool_executor.py, tools_bridge.py, DevVisual, DevLoopLiveFeed). HELD for founder approval.
- **#15 ORA skills usage**: 936 telemetry rows analyzed — 17/18 repo-skills have 100% error rate ("no project connected"). Pruning is not the problem; connectivity gate is.
- **#16 plan_latency_profile**: telemetry code added Iter 322 but zero rows written yet — same dead-write-path pattern as ORA learning.

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
