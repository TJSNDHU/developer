# AUREM CTO — Product Requirements Document (living)

**Last updated**: 2026-06-28 (Iter 334 · Auto-QA Agent) — see CHANGELOG.md for iter 332 (ship-gate P0 fix), 333 (Phase 1 correction rules), 334 (auto-QA agent)
**Live**: https://auremcto.com (⚠️ /api routes 520 since last deploy — infra routing issue, deployer RCA done, awaiting founder decision: retry redeploy vs support escalation)

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

### Iter 331-D · DELETE GATE (2026-07-28) — 3-layer file-deletion safety
- **Layer 1**: `scripts/check-safe-to-delete.sh` — catches lazy/dynamic imports + string-keyed
  routing refs (the exact class missed 3× with tool_executor.py). Verified: all 4 previously
  "approved" files flag ❌ (tools_bridge 15 refs, tool_executor 7, VisualFixtures 3,
  LoopLiveFeedDemo 2); a genuinely dead file flags ✅.
- **Layer 2**: mandatory approval template in `docs/DELETE_GATE.md` — no script output pasted →
  delete auto-rejected.
- **Layer 3**: quarantine-first policy (DeprecationWarning or `_deprecated/` move, 1-2 week prod
  log watch) before hard delete.
- **CI**: `delete-gate` job in ci.yml — PR deletions need "Script output:" block in description;
  pushes (Emergent auto-push) need docs/DELETE_GATE.md updated in the same push. YAML validated.
- Locks: `test_iter331_delete_gate.py` (3 tests).

### Iter 331-C · PROD /health starvation + Stripe 404 diagnosis (2026-07-28)
- **Fixed — event-loop starvation**: `_probe_stripe` (8 sequential sync Stripe HTTP calls) and
  `_probe_e2b` (sync 15s sandbox boot, `e2b.api.client_sync`) ran directly on the event loop →
  nginx `GET /health` upstream timeouts (110) + "No response returned" in PROD logs, timestamps
  matching the probe windows exactly. Both probes now offload via `asyncio.to_thread`.
  Proof: heartbeat harness — ZERO loop stalls >200ms during real probes (stripe ok 1.3s, e2b ok 0.6s).
  Lock: `test_iter331_health_probe_offload.py` (3 tests).
- **Diagnosed — PROD-env-only**: 3 monthly Stripe price IDs in prod env
  (`price_1TfXGf/TfXHp/TfXE1…2XYZ7cJIy2…`) belong to an OLD Stripe account → 404 → cron "1 broken"
  + monthly checkout at risk. FOUNDER ACTION: update prod env vars STRIPE_STARTER_PRICE_ID,
  STRIPE_PRO_PRICE_ID, STRIPE_TEAM_PRICE_ID with live-mode recurring price IDs from the CURRENT
  account (annual 3 already correct, `0Exg9gU93t` prefix).

### Iter 331-B · Items 4-7 closure (2026-07-28, post-deploy)
- **P0 FOUND & FIXED — `tool_executor.py` restored**: an earlier session deleted it as "approved
  dead code" but it's lazy-imported inside `invoke_local_tool` — EVERY chat tool-call crashed at
  runtime (`ModuleNotFoundError`, reproduced live). Restored from git (`8e2536a~1`), validated by
  its own suite (`test_iter209` 11/11 green) + live chat with tools. **Was live-broken on prod
  since 27-Jul deploys — needs redeploy.**
- **Item 4 · Deploy B / B1-race**: already implemented in Shell.jsx (`shouldDeferSessionMint` +
  3s fallback) with `Shell.iter329_b1_race.test.jsx` green — shipped to prod in today's deploy.
- **Item 5 · ORA learning resurrection (b→e) DONE with raw Mongo proof**:
  - (b) **Callsite reattach**: casual-gateway/advisor paths label `mode:"chat"` which bypassed the
    `(None,"A","B")` filter → council log + brain update silently dead on the main chat path.
    Filter now accepts `"chat"`; D/E stay excluded (BUG 5 safe). Lock: `test_iter331_learning_reattach.py`.
  - (c) **Live before/after proof**: ora_patterns wrote exact prompt file-paths into `hot_files`;
    council count 89→91; `project_brains.p_norepotest.updated_at` 2026-06-14 → 2026-07-28 06:10 with
    the decision text captured (`push_ops=1` log line).
  - (d) **Flags ON**: `ORA_CANARY_ENABLED=1` (was already set; nightly 02:30 run confirmed in
    ora_canary_runs) + `ENABLE_EVAL_CRON=1` added — startup logs show both armed.
  - (e) **Learning-health tile** live on /admin/architecture: GET `/admin/learning-health`
    (brains freshness → green/red, patterns, council 24h, canary last-run, flag badges) +
    `LearningHealthTile` (3 vitest).
- **BONUS FIX — /admin/architecture page was DEAD**: `Architecture()` (PersonaQualityTile +
  code-surface) was defined but never wired into renderPage — route silently fell to Overview.
  Added `case "arch"` + sidebar NAV entry. Verified live via Playwright screenshot.
- **Item 6**: `useChatSession.js` pruned — dead `loadHistory`/`loadingHistory`/no-op
  `refreshSessions` removed (zero callers, ChatPanel owns its own history loading).
- **Item 7 (names were stale)**: "queue-status-bar"→AgentStatusBar (already tested),
  "phase-stepper"→LoopStepBar (heavily tested). Real gap = **ShipStreakWidget**: 5-test suite
  added (hidden@0, pill render, milestone toast + lower-milestone marking, ack dedupe,
  aurem:shipped refetch).
- **Stale-test repairs**: `test_iter138` modernised (invoke_local_tool ctx arg + founder-gate
  test 2b + dynamic toolbelt-size assert). Suites: **210/210 vitest · touched-area pytest green**.

### Iter 331 · Systematic Closure (2026-07-28) — tech-debt zeroing before Phase 1
- **Section A** — 2 red vitest fixed: `LoopLiveFeed.iter329_task2_shipped_row.test.jsx` rewritten
  for Iter 330 phases (`submitting`/`handed-off`, poll phases removed). Added hand-off assertion:
  `streamLoopEvents` called with the loopId after POST resolves. **202/202 vitest green.**
- **Section B** — `OperationHistory.test.jsx` created: 7 regression tests (history hydration,
  Guard A parent churn → exactly ONE stream, Guard B/C post-terminal no-reopen, live rollback →
  terminal collapse + abort, (loop_id, op_type) dedupe, fetch fail-open, stream onError non-fatal).
- **Section C1** — ORA shadow-learning fail-open logging: both bare `except: pass` blocks in
  `chat.py` (session patterns + council log/brain update) now `logger.warning` with the exception.
- **Section C2 — DELETE LIST REJECTED WITH EVIDENCE** (founder decision needed):
  - `tools_bridge.py` → **NOT dead**: `services/orchestrator.py:20` imports it; orchestrator is
    live in 8 routers (chat.py:22, loop.py, admin.py…). Delete = backend crash.
  - `VisualFixtures.jsx` (/dev/visual) → **NOT dead**: Playwright Layer 2 fixture page used by
    `state_fixtures.spec.js` (7 fixtures) + `interaction_latency.spec.js`.
  - `LoopLiveFeedDemo.jsx` (/dev/loop-live-feed) → **NOT dead**: used by `a11y_journeys.spec.js`
    + `public_routes.spec.js`.
- **Section D1 · Bug 1 FIXED (PLAN gray during EXECUTE)** — root cause chain proven by repro:
  ChatPanel timeout-recovery (`setLoopPhase(active.phase…)` ~line 2478) and ship-gate hydration
  (`setLoopPhase("ship")` line 591) leak RAW engine phases (`plan/execute/verify/scan/ship/
  self_heal`) which did not exist in `PHASE_TO_STEP` → `active=0` → PLAN rendered gray "future".
  Fix (a): raw-phase aliases added to `PHASE_TO_STEP` (LoopStepBar.jsx). Fix (b): engine now emits
  `_narrate("plan","success")` at EXECUTE start (`loop_engine.py _do_execute`) — placed AFTER
  `state=EXECUTING` because emitting in `confirm()` would carry `awaiting_confirmation` and
  re-trigger PlanApprovalCard. Locks: 8 vitest (`LoopStepBar.iter331_raw_phase_alias.test.jsx`)
  + 3 pytest (`test_iter331_plan_narration.py`).
- **Section D2 · Bug 2 NO CODE BUG (ECG pulse-wave)** — live browser probe on preview
  `/dev/visual?state=step-executing`: `animationName=ecg-scroll, playState=running`, transform
  samples −43.7 → −5.3 → −24.5 over 700ms (**moving=True**), waveform visible in screenshot.
  Production symptom = stale bundle or Bug 1's phase-mapping class (no step ever "active").
  Regression lock added; founder re-verifies on prod after next deploy.
- **Stale test repairs (pre-existing reds, evidence-verified via git)**:
  `test_chat_history_returns_last_100_not_20` → asserts `[-200:]` (Iter 330 chat-vanish fix);
  `test_step_bar_forces_ship_success_on_terminal_completed_phase` → Option C regex for the
  Iter 329 nested Rule 0-a form.
- **Backend suite regression proof**: failing-file subset run on pre-change vs post-change code —
  set-diff shows ZERO new failures from Iter 331 (245-246 failures are old iter36-212 era debt).

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

**P0 — awaiting founder:**
- **REDEPLOY NEEDED**: prod is running with `tool_executor.py` deleted → every chat tool-call
  crashes there. Iter 331-B restore + learning reattach + admin tile must ship.
- **Iter 330+331 prod smoke test** (founder, 2 cycles): real ship → OperationHistory → rollback →
  SSE progress → collapsed row. Also re-check Bug 1 (PLAN green during EXECUTE) + Bug 2 (ECG wave)
  on prod AFTER deploying Iter 331.
- **#14 dead-code delete list** — REJECTED with evidence (see Iter 331 above). Founder must supply
  a corrected list; all 3 named files are live dependencies.
- **Tavily credits exhausted + Firecrawl probe timeouts** — 32 critical integration alerts on
  /admin/architecture (founder: top up tavily.com, check Firecrawl).

**P1 — next in queue:**
- **Deploy B** — Chat-history B1-race (`Shell.jsx` mint deferral with 3s fallback). (B3 `$slice: -200` already shipped.)
- **#3 · ORA learning resurrection** (b→e): callsite reattach → real Mongo before/after proof → canary/eval flags ON (shadow) → learning-health tile on `/admin/architecture`. (Step (a) fail-open logging DONE in Iter 331.)
- **Task 1 · ORA Canary + Admin Health Tile** — `ORA_CANARY_ENABLED` + `ENABLE_EVAL_CRON`, tile reads `project_brains.max(updated_at)`, RED if >24h stale.
- **#17 · Large-plan E2E** — real live loop with 21+ files through PLAN→EXECUTE→SCAN→integrity guard→ship gate.
- **Backend suite debt** — ~245 pre-existing failures (iter36-212 era source-assertion/env tests) +
  2 import-dead files (`test_iter138_acceptance_seven.py`, `test_iter209_citation_guard_and_tool_executor.py`
  import deleted `services.tool_executor`) + `test_iter212m163_aggression_chat.py` needs env at import.
  Founder call: repair wave vs prune list.

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

## Iter 339 · 2026-07-28 — Production deploy + dual verification
- Preview smoke test PASS (login, /auth/me + /auth/tokens leak-scan, /version, /health, frontend load)
- Deployed working-tree snapshot to prod (includes secret_leak_scan in Auto-QA + BUILD_INFO.txt fix)
- Post-deploy verification (per founder's correction — SHA is a label, not proof):
  - SHA check: prod /version = 05b5a310ce0a, matches preview ✓
  - Behavioral proof: ran `_run_secret_leak_scan` manually against prod
    (/auth/me + /auth/tokens) via new synthetic account qa-scan-bot@aurem.dev
    → both PASS, no sensitive keys. Raw curl double-check also PASS.
- New credential: qa-scan-bot@aurem.dev (prod synthetic QA account, see test_credentials.md)
- Next: Section 0 QA sandbox (user manual steps), Phase 2 Risk-Based Routing, Phase 3 Checkpoints/Rollback
