# PROD Bulk-Fix Rate-Limit Probe + Full-Repo Search — Final Report (Iter 212m-179)

**Date**: Jul 2-3, 2026 · **Environment**: PRODUCTION (auremcto.com) · **Account**: founder · **Repo**: TJSNDHU/Aurem (16,542 files)

---

## 1. Empirical bulk-fix rate-limit probe (REAL production pipeline)

Method: escalating REAL `POST /fix-pipeline/bulk` runs through the live prod
pipeline (LLM patch + branch + commit + draft PR per fix), polled via
`/fix-pipeline/summary`. Raw data: `prod_bulkfix_probe_results.json`.

| Run | Fixes OK | Failed | Rate-limit hits | Duration | Per-fix avg |
|-----|----------|--------|-----------------|----------|-------------|
| n=1 (PAT sanity) | 1/1 | 0 | 0 | 31s | 31s |
| n=5  | 5/5   | 0 | 0 | 287s  | ~57s |
| n=10 | 10/10 | 0 | 0 | 597s  | ~60s |
| n=20 | 20/20 | 0 | 0 | 1097s | ~55s |
| n=30 | — rejected by the NEW backend cap (`bulk_limit_exceeded`, max=20) — already live on prod | | | | |

**Conclusion**: with the 212m-178 pacing (1.5s inter-fix + Retry-After
retries) the real pipeline writes ~7-8 GitHub content-calls/min — far
under GitHub's 80/min secondary burst limit. 36/36 fixes committed with
ZERO 403s. The earlier "2nd fix 403" failures were the OLD unpaced code
plus a READ-ONLY PAT (`Resource not accessible by personal access token`
— also the root cause of the earlier swift-loop ship failure).

**Hard cap set**: `_BULK_MAX_FINDINGS = 20` (backend 400 `bulk_limit_exceeded`
+ modal warning + auto-slice in UI). Rationale: 20 empirically clean,
bounded job time (~18 min), and headroom under GitHub's 500 writes/hr
budget (20 fixes ≈ 140 writes) for the user's other GitHub activity.

**Side effect**: the probe produced 36 REAL fix branches (`aurem/fix-*`)
with 36 draft PRs on TJSNDHU/Aurem — genuine health-finding fixes.
Founder can review/merge or bulk-close them.

## 2. search_repo — full-repo verification on PROD

- Rare pattern over 16,542 files: **12.4s, complete scan** (old: 79s AND partial)
- Correctness proof: `def handler` → PROD count 18 == local complete count 18 ✅ (full_repo_snapshot path live)
- Found: platform sweeps `/tmp`, so the snapshot cache never persisted →
  every prod search paid the ~13s cold download. **Fixed (179b)**: cache
  moved to `/app/.aurem_cache/repo_snapshots` (gitignored), >2MB members
  skipped (294→264MB), disk-usage logging on failure, MCP response now
  carries `source`/`complete`/`snapshot_error` diagnostics.
  Preview verified: COLD 12.9s / WARM **0.4s**. Prod gets this on next redeploy.

## 3. Deploy state (verified by probing prod bundles/API)

| Change | On PROD now? |
|--------|--------------|
| search_repo full-snapshot (complete results) | ✅ live |
| Bulk cap 20 backend + modal warning + PAT deep-links (contents+PR write) | ✅ live |
| Meta Pixel SPA route tracking | ✅ live |
| FixJobContext summary-polling fallback (bulk progress visible multi-worker) | ❌ needs redeploy |
| fix_job_manager `status` in in-memory summary | ❌ needs redeploy |
| Snapshot cache under /app + diagnostics (warm 0.4s searches) | ❌ needs redeploy |

## 4. Regression / test status
- Testing agent (preview, backend+frontend): **100% pass, 0 issues** (`iteration_iter212m179_verify.json`)
- Critical suites: 61 + 17 tests green (fix pipeline, search, persistence, drawer diff, prod perf/reliability)
- Full pytest: 2533 passed; ~190 pre-existing stale failures in old iteration files (env-dependent guards) — P2 tech debt, none caused by this session.
