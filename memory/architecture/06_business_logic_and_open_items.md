# 06 — BUSINESS LOGIC (TIERS/QUOTA) & OPEN DECISIONS
(Load this LAST. Monetization rules + current punch list — check before starting any new work.)

## TIER / QUOTA SYSTEM
Source of truth: `services/subscription_tiers.py` (limits) + `services/scan_fix_quota.py` (fix-tool gating).

| Tier | Price/mo | Tasks/month | Fix tools | Bulk fix | Modes | Extras |
|---|---|---|---|---|---|---|
| Free | $0 | 10 | **none** (scans only) | ✗ | swift | — |
| Starter | $9 | 50 | vanguard-scan | ✗ | swift | brain memory |
| Pro | $19 | 300 | vanguard-scan + health-scan | ✗ | swift, pro | + parallel agents |
| Team | $49 | 400 | all 4 (vanguard, health, security, bug-hunt) | ✓ | swift, pro, maxx | + priority queue |
| Founder | $0 (internal) | unlimited | all 4 | ✓ | all | never billed |

**Core rule: 1 fix = 1 task.** No severity-based pricing — a critical fix and a minor fix cost the same quota unit. Scan-fix usage rolls into the SAME monthly task meter as chat tasks (`services/usage.py` merges `scan_fix_usage` into `tasks_this_month`).

**Quota gate contract** (`assert_can_fix`): `400 unknown_tool` / `403 fix_not_available_on_tier` / `403 bulk_fix_not_available` / `402 insufficient_tasks`. Deduction via `record_scan_fixes()` ONLY on success — failed fixes never burn tasks.

## RULES FOR THE AI DEVELOPER (hard constraints)
1. Never let a Free-tier user consume a fix — Free is scan-only by design.
2. Never introduce per-severity cost logic. If the business ever changes this, `scan_fix_quota.py` AND this file must be updated together.
3. Any new tool ships ONLY after it is explicitly mapped to tiers in `FIX_TOOLS_BY_TIER` — no undefined tier-gating.
4. Tier limits live ONLY in `subscription_tiers.py` — never hardcode a limit in a router, service, or frontend component.
5. If a feature changes what counts as "one task," update `scan_fix_quota.py`, `usage.py`, and this table together — they must never drift.
6. Unknown/invalid tier strings coerce to FREE (`_coerce`) — rely on this, don't add your own fallback.

## OPEN DECISIONS (resolve or explicitly flag as still-open before building on these areas)
1. **36 probe draft PRs** — created during empirical rate-limit testing, need cleanup. Do NOT build new fix-pipeline features until confirmed these stale PRs won't conflict.
2. **Vanguard CI ingest token (`AUREM_CI_INGEST_TOKEN`)** — waiting on user. Do NOT hardcode a placeholder token or assume an auth mechanism for `vanguard_ci.py` ingestion until resolved.
3. ~~HTTP Security Headers + Docker CIS rules~~ — **RESOLVED (2026-06)**: implemented. `http_headers` vuln class in Security Scan (`_scan_http_headers`, repo-level) + `docker` category in Health Scan (`_scan_docker_cis`, 9 CIS rules) with NEW badges in UI.

## STRICT INSTRUCTION FOR THE AI DEVELOPER
Before any task touching quota, tier gating, Vanguard CI, or security-header/CIS scanning: read this file first. If the task depends on one of the 3 open decisions above, STOP and flag it — never guess the intended behavior.
