# 04 — BACKEND: SERVICES (87 modules — `backend/services/`)
(Self-contained context module. System map: file 01. Routers that call these: file 03.)

## CATEGORIES

### AI Orchestration & Routing
`orchestrator.py`, `llm_router.py`, `smart_router.py`, `route_cache.py`, `mode_classifier.py`, `mode_b_council.py` (multi-LLM council), `mode_d_debugger.py`, `mode_e_auditor.py`, `mode_f_engage.py`, `parallel_agents.py`, `agents.py`, `llm.py` (Council A primary = `anthropic/claude-sonnet-4.5` since Iter 212m-193; on-boot probe + 15-min periodic re-probe, state persisted in `council_health_probes`), `ora_client.py`, `tools_bridge.py` (5 tool-call shape extractors incl. lenient XML fence recovery — Iter 212m-192)

### Scanners
`vanguard_scanner.py` (security), `architecture_health.py`, `bug_hunt_rules.py`, `design_linter.py`, `code_reviewer.py`, `post_task_scanner.py`, `integration_health.py`, `scan_cache.py`

### Fix Engine
`finding_fix_applier.py`, `fix_job_manager.py`, `fixed_findings.py` (PR-merge-gated ledger — pattern 2, file 01), `scan_fix_quota.py` (1 fix = 1 task — file 06), `task_diff.py`, `repo_heal.py`

### Repo Intelligence
`repo_context.py`, `codebase_indexer.py`, `repo_indexing.py`, `repo_map.py`, `graph_builder.py`, `github_cache.py`, `github_issues_context.py`, `file_selector.py`, `.aurem_cache` snapshotting (pattern 4, file 01)

### GitHub Write Path
`github_api_writer.py` (ONLY module that writes to GitHub — commits, branches, PRs), `github_oauth.py`, `github_deploy_service.py`

### Safety & Guards
`hallucination_guard.py`, `citation_guard.py` (both mandatory before any fix — pattern 5), `loop_safety.py`, `loop_verify.py`, `rate_limiter.py`, `sandbox_runner.py`, `vanguard_verify_agent.py`, `audit_log.py`

### Loop / Autonomous Mode
`loop_engine.py`, `loop_execute.py`, `loop_safety.py`, `loop_verify.py`

### Learning & Memory
`ora_learning.py`, `ora_fix_learning.py`, `ora_context.py`, `ora_council_logger.py`, `ora_council_retriever.py`, `project_brain.py`, `skill_usage.py`, `skill_context_injector.py`, `dev_skills.py`, `house_rules.py`, `thinking_hints.py`

### Billing & Business
`subscription_tiers.py` (single source of truth for tier limits), `billing_cron.py`, `financials.py`, `topup_alerts.py`, `usage.py` (monthly task meter), `founder_offer.py`-related flows

### Misc Infra
`feature_flags.py`, `db_backup.py`, `daily_digest.py`, `onboarding_email.py`, `error_translator.py`, `langfuse_tracing.py`, `external_services_registry.py`, `seo/`, `mfa.py`, `vault.py`, `tool_executor.py`, ~~`tools_bridge.py`~~ (moved to AI Orchestration), `local_tools.py`, `mcp_scoped_tools.py`, `url_fetcher.py`, `web_skills.py`, `vercel_skills.py`, `sandbox_runner.py`, `admin_analytics_cache.py`, `bin_context.py`, `deploy_logger.py`, `vanguard_audit.py`, `vanguard_config.py`, `generation_rules.py` (Iter 212m-190 · LLM persona-injected safety rules), `loop_full_scan.py` (Iter 212m-190 · 5-scanner Loop-Mode aggregator with depth gate + 3× auto-retry)

## RULES FOR THE AI DEVELOPER (hard constraints)
1. All LLM calls route through `llm_router.py`/`smart_router.py` — never call a provider SDK/API directly from a scanner, fixer, or feature module. Never hardcode a provider API key.
2. A fix MUST pass BOTH `hallucination_guard.py` and `citation_guard.py` before `finding_fix_applier.py` touches a repo. Non-negotiable.
3. All GitHub writes (commits, branches, PRs) go through `github_api_writer.py` — no ad-hoc GitHub write calls elsewhere.
4. New scanners plug into the existing scanner category and reuse `scan_cache.py` — never duplicate rule logic already in `vanguard_scanner`, `architecture_health`, `bug_hunt_rules`, `design_linter`, or `code_reviewer`.
5. Autonomous/loop features MUST respect `loop_safety.py` + `rate_limiter.py`. No loop may skip these.
6. Untrusted or LLM-generated code executes only inside `sandbox_runner.py` — never in the main process.
7. Tier limits live ONLY in `subscription_tiers.py`; fix-tool gating lives ONLY in `scan_fix_quota.py`. Never hardcode limits elsewhere.
8. Repo reads go through `repo_context.py` / `.aurem_cache` first (pattern 4) — never hit GitHub raw for content a snapshot already has (secondary rate limits).
9. `fixed_findings.py` writes must remain merge-gated: a finding is hidden from rescans, not deleted, until its `commit_sha` merges.
