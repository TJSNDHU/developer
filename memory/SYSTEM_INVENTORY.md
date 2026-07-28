# AUREM CTO — System Inventory (Single Source of Truth)

> **Purpose**: Persistent snapshot of what actually exists in the
> codebase, checked BEFORE any new build to prevent duplicate work.
> Not a marketing doc. Records live state including orphaned + half-
> built sections; do NOT auto-clean.
>
> **Owned by**: main agent workflow (E1). Update rules in
> `INVENTORY_CONTRACT.md` (pending Step 2-4 wiring).
>
> **Seeded**: 2026-07-27 from discovery pass over commit `2eea5442ddac`.
> **Last update**: 2026-07-27 (Iter 328 · auto-append wired + retroactive backfill for Iter 309/318/319/322/326/327 + ShipConfirmModal drift + LoopApi risk noted + Ripple #2 digest template verified).
> **Drift check**: Step-3 auto-append helper live at `services/inventory_service.py` (wired into loop ship-completion, fire-and-forget, fail-open).

---

## 1. Routers / Endpoints — 60 files, ~370 routes

All routers registered via `backend/main.py` under `/api/aurem-dev/`
prefix except where noted. Route counts from `grep '^@router\.'`.

### High-traffic (user-facing)
| File | Prefix | Routes | Purpose |
|---|---|---|---|
| `routers/auth.py` | `/auth` | 10 | signup, login, /me, tokens, set-track, logout, revoke-all-sessions, google/session, robot-guide, 2FA verify |
| `routers/chat.py` | `/chat` | 13 | `/stream` (streaming LLM), `/send`, `/history` (100 turns), `/sessions`, `/turn/shipped`, `/task-followup` |
| `routers/cto_projects.py` | `/cto` | 27 | project CRUD, tasks, warm_agents, scaffold, brain updates, indexing |
| `routers/loop.py` | `/loop` | 11 | start, {id}/status, {id}/stream (SSE), {id}/confirm, {id}/cancel, active, admin/loop-inspect |
| `routers/ora_chat.py` | `/ora-chat` | 19 | admin ORA chat surfaces |
| `routers/admin.py` | `/admin` | **95** | admin toolbox (see sub-groups below) |

### Payments / Billing
| File | Prefix | Routes | Purpose |
|---|---|---|---|
| `routers/payments.py` | (none) | 6 | Stripe checkout, webhook, portal, sub status, cancel, topup |
| `routers/founder_offer.py` | `/founder-offer` | 5 | first-72h founder discount + real-fix trigger |
| `routers/usage.py` | `/usage` | 3 | /me token balance, budgets, wallet |

### Deploy / Infra
| File | Prefix | Routes | Purpose |
|---|---|---|---|
| `routers/deploy.py` | `/deploy` | 9 | legacy deploy history/run/retry/cancel |
| `routers/github_deploy.py` | `/github-deploy` | 6 | GitHub Actions deploy trigger + status |
| `routers/hosted_deploy.py` | `/hosted-deploy` | 4 | Emergent-hosted deploy pipeline |
| `routers/vercel.py` | `/integrations/vercel` | 4 | Vercel platform token → project deploy |
| `routers/domain.py` | `/domain` | 3 | Cloudflare DNS + custom domain wiring |
| `routers/managed_db.py` | `/managed-db` | 6 | Aurem-managed Postgres wrapper |
| `routers/supabase.py` | `/supabase` | 8 | Supabase mgmt API — org, project create, downgrade |

### Codebase Health / Vanguard / Security
| File | Prefix | Routes | Purpose |
|---|---|---|---|
| `routers/codebase_health.py` | `/codebase-health` | 5 | full-repo scan trigger + findings |
| `routers/security_scan.py` | `/security-scan` | 2 | one-off scan + `_scan_text` fn (Iter 319 rescue) |
| `routers/findings.py` | `/findings` | 5 | list, ack, resolve, false-positive |
| `routers/fix_pipeline.py` | `/fix-pipeline` | 7 | bulk-fix orchestration |
| `routers/harden.py` | `/harden` | 1 | server hardening scan |
| `routers/vanguard_ci.py` | `/vanguard` | 3 | CI-side Vanguard scan ingest |
| `routers/admin_vanguard.py` | `/admin/vanguard` | 2 | admin Vanguard tools |
| `routers/qa_probe.py` | `/qa` | 1 | QA test probe |
| `routers/admin_qa.py` | `/admin/qa` | 1 | QA dashboard data |
| `routers/integrity_log.py` | (none) | 1 | integrity log ingest |

### GitHub / Repo
| File | Prefix | Routes | Purpose |
|---|---|---|---|
| `routers/github_oauth.py` | `/github/oauth` | 5 | OAuth start/callback/disconnect |
| `routers/github_bot.py` | `/github` | 2 | GitHub App webhook receiver |
| `routers/oauth.py` | (none) | 6 | generic OAuth (Google) |
| `routers/repo_indexing.py` | `/repos` | 1 | index a repo |
| `routers/repo_status.py` | `/cto/projects` | 3 | per-project repo status/re-scan |

### Onboarding / Personal Track
| File | Prefix | Routes | Purpose |
|---|---|---|---|
| `routers/onboarding.py` | (none) | 2 | onboarding emails + choose-track |
| `routers/personal_track.py` | `/personal-track` | 1 | personal-track admin |
| `routers/scaffold.py` | `/scaffold` | 15 | scaffold drafts + gen + review + ship (Personal Track "build" pipeline) |
| `routers/stacks.py` | `/stacks` | 2 | tech-stack catalog |
| `routers/chat_commits.py` | `/chat-commits` | 3 | commit history in chat panel |

### Misc / Utility
| File | Prefix | Routes | Purpose |
|---|---|---|---|
| `routers/feature_window.py` | `/feature-window` | 1 | system-map page data |
| `routers/diagram.py` | `/diagram` | 1 | Mermaid diagram generator |
| `routers/advisor_context.py` | `/api/aurem-dev` | 1 | advisor context probe |
| `routers/support.py` | `/support` | 3 | support ticket create/list |
| `routers/mfa.py` | `/admin/2fa` | 4 | admin 2FA enroll/verify |
| `routers/mcp.py` | `/mcp` | 7 | MCP protocol endpoint (Model Context Protocol) |
| `routers/unlock.py` | `/unlock` | 2 | account-tier unlock requests |
| `routers/trust.py` | (none) | 5 | trust-score tracking |
| `routers/trust_level.py` | `/api/aurem-dev/me` | 2 | user trust level |
| `routers/upload.py` | `/upload` | 1 | file upload (OpenRouter-based OCR) |
| `routers/automations.py` | `/automations` | 6 | user automation rules |
| `routers/shipwall.py` | `/wall` | 7 | public ship-wall gallery |
| `routers/wrapped.py` | `/wrapped` | 1 | year-in-review page |
| `routers/suggestions.py` | `/suggestions` | 3 | founder suggestions |
| `routers/thinking_hints.py` | (none) | 7 | "thinking..." UI hints |
| `routers/notify_interest.py` | (none) | 1 | tool interest signup |
| `routers/engagement.py` | (none) | 4 | engagement events tracking |
| `routers/vault.py` | `/vault` | 1 | encrypted secrets vault |
| `routers/lint_preview.py` | `/lint` | 1 | pre-commit lint preview |
| `routers/version.py` | `/api/aurem-dev` | 2 | `/version` build info, health |

### Admin sub-groups (all under `admin.py` = 95 routes)
`/admin/bin-tracker`, `/admin/feature-flags`, `/admin/llm-credits`,
`/admin/parliament-live`, `/admin/architecture`, `/admin/ops`,
`/admin/inspect-loop/{id}`, `/admin/inspect-speed-diagnostic`,
`/admin/inspect-scope-drift`, `/admin/brain/{project_id}`.
Plus `routers/admin_bin.py` (+8 OpenRouter BIN tracker routes).

### Dev-only (gated)
- **`routers/dev_sse_probe.py`** (`/aurem-dev/_iter309_probe` +3):
  404s in prod unless `AUREM_ENABLE_SSE_PROBE=1`. Test-only synthetic
  SSE stream for reconnect validation.

### `main.py` inline endpoints (8)
`/api/health`, `/api/healthz`, `/api/aurem-dev/health/ora`,
`/healthz`, `/health`, `/ping`, `/api/v1/health`, `/api/_diag/memory`
(tracemalloc gated by `ENABLE_TRACEMALLOC`).

---

## 2. Background jobs / schedulers (all `asyncio.create_task`, no APScheduler)

Fired on FastAPI startup from `main.py`:

| Task | Purpose | Cadence | Gate |
|---|---|---|---|
| `schedule_daily_digest` (`services/daily_digest.py`) | daily email digest | once/day at `DIGEST_HOUR_UTC=6` | always on |
| `_loop_housekeeping` (main.py:296) | orphaned running loops → FAILED, lock TTL | periodic | always on |
| `_ensure_loop_safety_indexes` | Mongo index create | one-shot | always on |
| `_orphan_running_fix_jobs` | mark stuck fix jobs failed | one-shot | always on |
| `_backfill_dev_users_created_at` | migration | one-shot | always on |
| `_backfill_dev_users_track` | migration | one-shot | always on |
| `_ensure_ora_learning_indexes` | Mongo index | one-shot | always on |
| `_ensure_iter272_indexes` | Mongo index | one-shot | always on |
| `_warm_codebase_index` | pre-warm codebase index | one-shot | always on |
| `nudge_cron` (`services/onboarding_email.py`) | onboarding nudge emails | 1 h loop | `ENABLE_ONBOARDING_NUDGE=1` |
| `backup_cron` (`services/db_backup.py`) | Mongo `mongodump` | interval | `ENABLE_DB_BACKUP=1` |
| `canary_cron` (`services/ora_chat/canary.py`) | ORA canary LLM calls | interval | `ORA_CANARY_ENABLED` |
| `_supabase_sweeper_cron` | orphaned Supabase project cleanup | interval | `ENABLE_SUPABASE_SWEEPER` |
| `_preview_sweeper_cron` | orphaned preview sandbox cleanup | interval | `ENABLE_PREVIEW_SWEEPER` |
| `_schedule_daily_evals` | daily LLM eval runs | daily at `EVAL_HOUR_UTC` | `ENABLE_EVAL_CRON` |
| `_bg_bootstrap` | misc post-boot warmup | one-shot | always on |
| `_probe_longcat` (services/llm.py:550) | LongCat model probe | one-shot | conditional |
| `_periodic_longcat_reprobe` | LongCat model reprobe | 15-min loop | conditional |
| `_probe_loop_linters` | ruff/eslint availability probe | one-shot | always on |

**On-request `asyncio.create_task` fire-and-forget sites** (dozens):
each chat turn spawns 5+ side-effect tasks (`_QM.score_async`,
`maybe_log_ora_escalation`, `extract_session_patterns`,
`update_brain_from_conversation`, `record_turn`); each
`/cto/projects` mutation spawns brain-update + task-brain-update;
each fix job spawns `_run_bulk_job`; each deploy spawns
`_run_deploy_remote`.

---

## 3. MongoDB collections — ~110 distinct

**Auth/Users**: `dev_users`, `login_attempts`, `oauth_states`,
`oauth_codes`, `api_keys`, `settings`, `ui_settings`,
`nexus_credentials`

**Chat / ORA**: `chat_sessions`, `ora_chat_pin_attempts`,
`ora_chat_usage`, `ora_council_logs`, `ora_learning_logs`,
`ora_patterns`, `ora_review_log`, `ora_reviewer_errors`,
`ora_scan_learning`, `ora_skill_usage`, `ora_fix_learning`,
`ora_hallucination_log`, `ora_canary_runs`, `ora_eval_runs`,
`parliament_log`, `intent_classifications`,
`council_health_probes`

**Loop engine**: `loop_sessions`, `loop_events`, `loop_errors`,
`loop_failures`, `loop_locks`, `loop_plans`, `loop_run_log`,
`loop_backups`

**Projects / CTO**: `cto_projects`, `cto_tasks`, `cto_settings`,
`cto_notification_dismissals`, `cto_codebase_index`,
`cto_founder_suggestions`, `cto_open_findings`, `cto_maxx_usage`,
`cto_payments`, `cto_token_grants`, `cto_support`,
`cto_support_messages`, `cto_automations`, `project_brains`,
`project_graphs`, `repo_contexts`, `repo_context_timings`,
`repo_index`, `repo_cleanup_audit`, `repo_heal_audit`

**Scans / Health**: `codebase_health_scans`, `finding_fixes`,
`fixed_findings`, `fix_jobs`, `scanner_feedback`, `scan_fix_usage`,
`scan_rate_limits`, `quality_alerts`, `quality_scores`,
`post_task_scans`, `warm_start_jobs`, `smoke_test_kv`,
`smoke_test_runs`

**Payments / Financials**: `cto_payments`, `billing_cron_runs`,
`financial_settings`, `topup_alerts`, `email_offers`,
`founder_offer`, `onboarding_token_wallets`

**Deploy**: `aurem_cto_deploy_configs`, `aurem_cto_deploy_runs`,
`aurem_cto_domain_configs`, `aurem_cto_server_hardenings`,
`aurem_cto_public_gallery`, `aurem_cto_unlock_requests`,
`aurem_cto_chat_commits`, `deploy_events`, `github_connections`,
`github_deployments`, `supabase_projects`, `preview_sandboxes`

**Vanguard / Audit**: `vanguard_audit`, `vanguard_ci_findings`,
`vercel_tool_audit`, `aurem_cto_vault_audit_log`,
`cto_vault_audit_log`, `admin_audit`, `audit_log`

**Referrals / Growth**: `referrals`, `referral_clicks`,
`referral_profiles`, `verified_referrals`, `user_seo_claims`

**System**: `app_config`, `admin_settings`, `feature_flags`,
`integration_health`, `integration_health_history`,
`external_uptime_pings`, `frontend_errors`, `thinking_hints`,
`thinking_hints_config`, `tool_notify_interest`,
`onboarding_emails`, `issues_cache`

**Scaffold / Personal Track**: `scaffold_drafts`,
`scaffold_generations`, `scaffold_scan_overrides`

---

## 4. Frontend routes / pages — 49 page files, ~65 registered routes

Registered in `frontend/src/App.jsx`.

### Public / Marketing
`/` Landing · `/both` Both · `/why-ora` WhyOra · `/vs/devin` VsDevin
· `/vs/cursor` → devin redirect · `/pricing` Pricing · `/demo` Demo
· `/wrapped` Wrapped · `/wall` ShipWall

### Auth / Onboarding
`/login`, `/signup`, `/oauth-finish`, `/choose-track` (lazy →
`pages/personal/ChooseTrack.jsx`)

### Personal Track (lazy-loaded, `pages/personal/*`)
`/build` BuildHome · `/build/:draftId` DraftReview ·
`/build/:draftId/ship` ShipProgress · `/build/:draftId/success`
BuildSuccess · plus `PreviewPanel.jsx`, `_shell.jsx`

### Main app
`/dashboard`, `/ora` OraDirect, `/integrations`, `/deploy`, `/domain`,
`/settings`, `/profile` (alias→Settings), `/tokens`, `/analytics`,
`/projects`, `/automations`

### Codebase-health family (multiple aliases → same component)
`/tools`, `/tools/bug-hunt`, `/tools/health-scan`, `/codebase-health`,
`/health`, `/bug-hunt` (separate `BugHunt.jsx`)

### Admin (26 routes)
`/admin`, `/admin/overview`, `/admin/integrations`, `/admin/financials`,
`/admin/vanguard`, `/admin/system-stats`,
`/admin/observability` (alias→system-stats),
`/admin/api-keys`, `/admin/system-health`,
`/admin/inspect-loop/:loopId`,
`/admin/inspect-speed-diagnostic`, `/admin/inspect-scope-drift`,
`/admin/qa`, `/admin/personal-track`, `/admin/ora-chat`,
`/admin/bin-tracker`, `/admin/feature-flags`, `/admin/llm-credits`,
`/admin/parliament-live`, `/admin/architecture`, `/admin/ops`,
`/admin/brain/:projectId`,
`/admin/system-map` (→FeatureWindow)

### Policy (single `PolicyPage` with 10 slugs)
`/privacy`, `/terms`, `/acceptable-use`, `/cookie-policy`,
`/cookie-preferences`, `/refund-policy`, `/ai-code-processing`,
`/subprocessors`, `/dpa`, `/security`, `/status`

### 🚧 Dev / experimental / **UNLINKED from any nav** (orphaned routes)
- `/dev/loop-live-feed` → `LoopLiveFeedDemo.jsx`
- `/dev/visual` → `VisualFixtures.jsx`
- `/sidebar-preview` → `SidebarPreview.jsx`
- `/dashboard-preview-v2` → `DashboardPreviewV2.jsx`
- `/feature-window` → `FeatureWindow.jsx` (also admin-only alias)

### 404 catch-all → Landing

---

## 5. Env vars / feature flags — ~150 distinct

### Core (protected, never delete)
`MONGO_URL`, `DB_NAME`, `APP_URL`, `EMERGENT_LLM_KEY`, `JWT_SECRET`

### LLM providers
`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`,
`GROQ_API_KEY`, `ORA_API_KEY`, `ORA_BASE_URL`,
`OPENROUTER_FREE_MODELS`, `GLM_MODEL`, `GROQ_MODEL`,
`DEEPSEEK_DIRECT_MODEL`, `SCAFFOLD_REVIEWER_MODEL`, `VERIFIER_MODEL`,
`CEO_RESCUE_MODEL`, `MERMAID_DIAGRAM_MODEL`, `AUREM_VISION_MODEL`,
`ADVISOR_VISION_MODEL`, `ADVISOR_VISION_FAILOVER`,
`AUREM_VISION_FALLBACK_MODEL`, `MERMAID_DIAGRAM_FAILOVER`

### LLM tuning
`LLM_ADVISOR_MAX_TOKENS`, `LLM_ANALYSIS_MAX_TOKENS`,
`LLM_CHAT_MAX_TOKENS`, `CEO_PRIMARY_TIMEOUT_S`,
`DEEPSEEK_DIRECT_TIMEOUT_S`, `GROQ_TIMEOUT_S`, `CHAT_HARD_TIMEOUT_S`,
`ORCH_PER_TURN_BUDGET_S`, `ORCH_FINAL_ROUND_RESERVE_S`,
`ORA_BREAKER_COOLDOWN_S`, `ORA_BREAKER_FATAL_COOLDOWN_S`,
`ORA_QUICK_PIN`, `ORA_LEARNING_DISABLED`, `ORA_LEARNING_HOURLY_CAP`,
`ORA_REGEN_ON_FABRICATION`, `LITELLM_ROUTER_ENABLED`,
`COUNCIL_B_GLM_ENABLED`

### Stripe (Payments)
`STRIPE_SECRET_KEY`, `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET`,
`STRIPE_CALL_TIMEOUT`, `STRIPE_STARTER_PRICE_ID`,
`STRIPE_STARTER_ANNUAL_PRICE_ID`, `STRIPE_PRO_PRICE_ID`,
`STRIPE_PRO_ANNUAL_PRICE_ID`, `STRIPE_TEAM_PRICE_ID`,
`STRIPE_TEAM_ANNUAL_PRICE_ID`

### GitHub
`GITHUB_TOKEN`, `GITHUB_ORG`, `GITHUB_REPO`, `GITHUB_WEBHOOK_SECRET`,
`GITHUB_ACTIONS_TOKEN`, `AUREM_ORG_GITHUB_APP_TOKEN`,
`AUREM_ORG_NAME`, `AUREM_ORG_DEFAULT_BRANCH`, `AUREM_GITHUB_REPO`

### Deploy / Infra
`AUREM_VERCEL_PLATFORM_TOKEN`, `VERCEL_API_TOKEN`,
`VERCEL_PLATFORM_TEAM_ID`, `VERCEL_DEPLOY_HOOK_URL`,
`VERCEL_GIT_COMMIT_SHA`, `VERCEL_BANDWIDTH_ALERT_GB`,
`VERCEL_BANDWIDTH_KILL_GB`, `CLOUDFLARE_API_TOKEN`,
`CLOUDFLARE_ZONE_ID`, `SUPABASE_MANAGEMENT_TOKEN`, `SUPABASE_ORG_ID`,
`SUPABASE_DB_PASSWORD_SALT`, `SUPABASE_DEFAULT_REGION`,
`SUPABASE_DOWNGRADE_POLICY`, `E2B_API_KEY`, `AUREM_MASTER_KEY`,
`WORKSPACE_PATH`

### Other integrations
`RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `DIGEST_FROM`,
`TAVILY_API_KEY`, `FIRECRAWL_API_KEY`, `SENTRY_DSN`, `SENTRY_ENV`,
`SENTRY_RELEASE`, `SENTRY_TRACES_SAMPLE_RATE`,
`SENTRY_PROFILES_SAMPLE_RATE`, `LANGFUSE_BASE_URL`,
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `REDIS_URL`

### Feature toggles / gates
- **Cron gates**: `ENABLE_DB_BACKUP`, `ENABLE_ONBOARDING_NUDGE`,
  `ENABLE_PREVIEW_SWEEPER`, `ENABLE_SUPABASE_SWEEPER`,
  `ENABLE_EVAL_CRON`
- **Behaviour gates**: `ORA_CANARY_ENABLED`, `ORA_LEARNING_DISABLED`,
  `DISABLE_UPSTREAM_TOOLS`, `UPSTREAM_TOOLS_COOLDOWN_S`,
  `VANGUARD_VERIFY_ENABLED`, `VANGUARD_VERIFY_BLOCK_LEVEL`,
  `CEO_RESCUE_ENABLED`, `AUREM_QA_MODE`, `AUREM_QA_TOKEN`
- **Dev-only**: `AUREM_ENABLE_SSE_PROBE` (prod OFF)
- **Rate limiting**: `RATE_LIMIT_DISABLED`, `RATE_LIMIT_MAX_BUCKETS`,
  `ENABLE_TRACEMALLOC`
- **Ingest**: `AUREM_CI_INGEST_TOKEN`,
  `BRAIN_V2_FULL_REFRESH_EVERY_N_TASKS`

### Build / Env identity
`AUREM_COMMIT_SHA`, `AUREM_BUILT_AT`, `AUREM_ENV`,
`AUREM_PROD_ORIGIN`, `AUREM_PUBLIC_BASE_URL`, `AUREM_UPSTREAM_URL`,
`AUREM_DEPLOY_BRANCH`, `AUREM_DEPLOY_COMMIT`, `BUILD_HASH`,
`GIT_COMMIT`, `GIT_COMMIT_SHA`, `HOSTNAME`, `NODE_ENV`, `APP_ENV`,
`ENVIRONMENT`, `PRODUCTION_ENV`, `RENDER_ENV`, `EMERGENT_DEPLOY_ID`,
`EMERGENT_JOB_ID`, `SLOW_API_MS`

### Admin identity
`ADMIN_EMAIL`, `ADMIN_EMAILS`, `FOUNDER_EMAILS`

### DB-backed feature flags (`feature_flags` collection)
Infrastructure at `services/feature_flags.py`:
`is_enabled(flag, user_id, tier)`, admin toggle endpoint, 60 s cache,
`invalidate_cache()`.
⚠️ **`grep` shows ZERO call sites in the codebase actually check
`is_enabled(...)`.** System is fully built but unused. Only the
docstring example (`new_analytics_v2`) mentions it. **REUSE
CANDIDATE** for any new toggle work.

---

## 6. Files that look ORPHANED (imported nowhere)

### Dead services (14, `backend/services/`)

| File | Notes |
|---|---|
| `services/agents.py` | multi-agent scaffolding — abandoned; still contains OpenRouter refs |
| `services/boilerplate_audit.py` | boilerplate detection — never wired |
| `services/dev_skills.py` | dev-skill registry — only self-imports + `tool_executor` (also dead) |
| `services/generation_rules.py` | empty scaffolding |
| `services/github_cache.py` | GitHub API caching — never wired |
| `services/github_deploy_service.py` | superseded by `routers/github_deploy.py` inline logic |
| `services/loop_audit_log.py` | ⚠️ **NOT dead** — imported via `from services import loop_audit_log as _lal` (Iter 318 uses it). Grep false-positive. |
| `services/loop_outcomes.py` | loop-outcome analytics — dead |
| `services/reasoning_evals.py` | reasoning eval suite — dead |
| `services/skill_usage.py` | skill-usage tracking — dead |
| `services/smart_router.py` | intent smart-router — dead (replaced by `intent_gateway`) |
| `services/supabase_provisioner.py` | superseded by `routers/supabase.py` inline |
| `services/tool_executor.py` | tool execution — dead |
| `services/tools_bridge.py` | tool bridge — dead |

### Orphaned frontend routes (registered but not linked from any nav)
- `LoopLiveFeedDemo.jsx` (`/dev/loop-live-feed`)
- `VisualFixtures.jsx` (`/dev/visual`)
- `SidebarPreview.jsx` (`/sidebar-preview`)
- `DashboardPreviewV2.jsx` (`/dashboard-preview-v2`)

### Feature-flag system
See §5 — fully built, zero consumers. REUSE CANDIDATE.

---

## 7. Third-party integrations

Live HTTP callouts detected in code:

| Service | Purpose | Env vars |
|---|---|---|
| **Anthropic** (`api.anthropic.com`) | Council LLM | `ANTHROPIC_API_KEY` (or via Emergent LLM key) |
| **OpenRouter** (`openrouter.ai/api`) | Council LLM (multi-model) | `OPENROUTER_API_KEY`, `OPENROUTER_FREE_MODELS` |
| **DeepSeek** (`api.deepseek.com`) | cheap LLM tier | `DEEPSEEK_API_KEY` |
| **Groq** (`api.groq.com/openai`) | fast LLM tier | `GROQ_API_KEY` |
| **Emergent LLM Key** | universal proxy (OpenAI/Anthropic/Gemini/Sora) | `EMERGENT_LLM_KEY` |
| **ORA proxy** | custom internal LLM router | `ORA_API_KEY`, `ORA_BASE_URL` |
| **Stripe** (`api.stripe.com`) | payments + subs + portal + webhooks | `STRIPE_SECRET_KEY`, 10× price IDs, webhook secret |
| **GitHub** (`api.github.com`, `raw.githubusercontent.com`) | OAuth, repo r/w, PR/issue create, GitHub App | `GITHUB_TOKEN`, `AUREM_ORG_GITHUB_APP_TOKEN`, webhook secret |
| **Vercel** (`api.vercel.com`) | managed deploy, bandwidth monitor | `VERCEL_API_TOKEN`, `AUREM_VERCEL_PLATFORM_TOKEN` |
| **Cloudflare** (`api.cloudflare.com`) | DNS mgmt for custom domains | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ZONE_ID` |
| **Supabase** (`api.supabase.com`) | managed Postgres provisioning | `SUPABASE_MANAGEMENT_TOKEN`, `SUPABASE_ORG_ID` |
| **Resend** (`api.resend.com`) | transactional email | `RESEND_API_KEY`, `RESEND_FROM_EMAIL` |
| **Tavily** (`api.tavily.com`) | web search | `TAVILY_API_KEY` |
| **Firecrawl** (`api.firecrawl.dev`) | web scraping | `FIRECRAWL_API_KEY` |
| **E2B** (`e2b.dev`) | sandboxed code exec | `E2B_API_KEY` |
| **Sentry** (`sentry.io`) | error tracking | `SENTRY_DSN` |
| **Langfuse** (`us.cloud.langfuse.com`) | LLM observability | `LANGFUSE_PUBLIC_KEY`, `SECRET_KEY`, `BASE_URL` |
| **Frankfurter** (`api.frankfurter.dev`) | FX rates — one call site, probably legacy | none |
| **MCP** (`modelcontextprotocol.io`) | Model Context Protocol integration | via `routers/mcp.py` |
| **LongCat** (Meituan) | LLM model, probe + reprobe | via OpenRouter |

---

## 8. Surprising / genuinely-built subsystems most surfaces don't advertise

### Built subsystems
- **`/wrapped`** — Spotify-style "year in review" for dev stats
- **`/wall` (ShipWall)** — public gallery of AUREM-shipped commits
- **`/vs/devin`** — competitor comparison landing (Cursor → devin)
- **`/dev/loop-live-feed`** — full narration-feed dev harness (Iter 275)
- **`/admin/parliament-live`** — real-time Parliament LLM call viewer
- **`/admin/bin-tracker`** — OpenRouter BIN card tracker (payments infra)
- **`/admin/inspect-scope-drift`** — scope-drift audit dashboard (Iter 314)
- **`/admin/ops`** (`OpsRecipes.jsx`) — ops runbooks page
- **`/admin/brain/:projectId`** — dumps full project brain state
- **`/admin/architecture`** — architecture-map viewer
- **`/admin/personal-track`** — Personal Track (Build) admin

### MCP protocol server (`routers/mcp.py`, 7 routes)
Full MCP endpoint set — kicks off scans via `asyncio.create_task`.
AUREM is exposable to Claude Desktop / other MCP clients. Probably
not marketed.

### Vault subsystem
`routers/vault.py` + `services/vault_service.py` +
`aurem_cto_vault_audit_log` + `cto_vault_audit_log`. Encrypted
secrets vault per-project, backed by `AUREM_MASTER_KEY`.

### Trust-level system
`routers/trust.py` + `routers/trust_level.py` — 5 endpoints tracking
per-user "trust score" with tier gating logic.

### Personal Track (Build wizard)
Full lazy-loaded flow at `frontend/src/pages/personal/*`:
ChooseTrack → BuildHome → DraftReview → ShipProgress → BuildSuccess.
Backed by `routers/scaffold.py` (15 routes: drafts, generations,
scan overrides).

### ORA eval / canary system
`ora_canary_runs`, `ora_eval_runs` collections + `canary_cron` +
`_schedule_daily_evals`. Full LLM-quality regression suite running
daily behind env-gate.

### Brain V2
`project_brains` collection + `BRAIN_V2_FULL_REFRESH_EVERY_N_TASKS`
env + `update_brain_from_conversation` + `update_brain_after_commit`
+ `update_brain_after_task` + admin route `/admin/brain/:projectId`.
Persistent per-project memory — every chat turn + task + commit
updates the brain. Major infra.

### Referral / growth pipeline
`referrals`, `referral_clicks`, `referral_profiles`,
`verified_referrals`, `user_seo_claims`. Full pipeline exists in DB
+ code but no clear founder-facing entry point currently.

### Vanguard CI ingest
`vanguard_ci_findings` + `AUREM_CI_INGEST_TOKEN` +
`routers/vanguard_ci.py`. External CI can POST findings back into
AUREM. Full ingest pathway with token auth.

### Preview sweeper + Supabase sweeper
Two cron-gated janitors that clean orphaned preview sandboxes and
orphaned Supabase projects. Gated OFF by default.

---

## ⚠️ Half-built / probably broken

- **Feature-flag system** — infrastructure complete, ZERO consumers.
- **14 dead service files** (see §6). Safe to delete after `git log`
  sanity pass — separate cleanup task.
- **`ChooseTrack`** page exists but linking from Signup flow is
  unverified.
- **`/dashboard-preview-v2`** — alternate Dashboard, unreferenced.
- **4 overlapping deploy backends** — `hosted_deploy.py`, `deploy.py`,
  `github_deploy.py`, `vercel.py`. At least one is likely legacy.
- **Dual OAuth handlers** — `routers/oauth.py` (Google generic) vs
  `routers/github_oauth.py`. Overlap likely.
- **`services/loop_audit_log.py`** — appears "unused" by naive grep,
  IS live-imported via alias form. False-positive dead entry.
- **`ShipConfirmModal.jsx` (legacy) vs `ShipPendingCard.jsx` (loop-mode)**
  drift [Iter 328]: pre-Loop chat used `ShipConfirmModal` gated behind
  `MessageBubble`, which showed per-file +/- diff counts, Vanguard
  preflight pill, and a full confirm step before shipping. Loop-mode
  replaced this path with `ShipPendingCard`, which currently ships a
  bare "Ship to GitHub" button — **the diff preview + Vanguard verdict
  are NOT shown**. This is a safety regression (blind ship risk;
  session already saw a truncated README nearly ship). Fix planned:
  enrich `ShipPendingCard` (Deploy 2) — do NOT revert to
  `ShipConfirmModal`. `ShipConfirmModal.jsx` is now legacy but still
  imported via `MessageBubble.jsx` — leaving it in place, do not
  delete until Loop mode ship parity is confirmed.
- **`services/api/loopApi.js` duplication risk** (Ripple #4) [Iter 328]:
  `loopApi.js` exports `getLoopStatus` — if any admin surface (e.g.
  `/admin/inspect-loop`) fetches loop status via its own axios call
  instead of using the shared helper, contract drift is possible.
  DEFERRED — noted here as a known risk, not currently blocking.
  Refactor task: unify all `loop/{id}/status` fetchers behind
  `loopApi.getLoopStatus`. Assigned to future cleanup pass.

---

## 📮 Ripple verification log (per-iter)

- **Ripple #2 — daily digest / topup_alerts email template** [Iter 328,
  2026-07-27]: **VERIFIED — no fix needed.**
  - `services/daily_digest._render_text()` renders user/task/cost
    stats only; does NOT include integration status text. Digest
    email is unaffected by warn/broken/ok reclassification.
  - `services/topup_alerts._render_email()` renders DERIVED severity
    headers ("🚨 CRITICAL" / "⚠️ WARNING"), not raw probe status.
    `classify()` maps `warn` + money-keyword regex → `critical`.
    Tavily 432's summary "Credits exhausted or rate-limited" matches
    the pattern → correctly escalates to critical alert (this is
    intentional: credits are top-up-actionable, not cosmetic).
  - **Conclusion**: post-Iter 326 A reclassification, the email
    templates render the correct severity by construction. No sync
    change to templates.

---

## Bottom-line snapshot

- **60 router files, ~370 endpoints.** `admin.py` alone = 95.
- **19 background tasks** at boot, 6 env-gated OFF by default.
- **~110 Mongo collections** actively referenced.
- **49 frontend pages**, 4 orphaned dev/experimental routes in prod bundle.
- **~150 env vars.**
- **14 dead service files** in `backend/services/`.
- **Feature-flag infra built + unused** — kill-switch/canary system ready to wire.
- **MCP protocol server is live** — AUREM is an MCP-compatible tool provider.
- **Brain V2 memory system** runs on every chat turn + task + commit.
- **Referral/growth pipeline** exists in DB + code, no visible entry point.

---

## Update log

| Date       | Iter | Change                              |
|------------|------|-------------------------------------|
| 2026-07-27 | seed | Initial seeding from discovery pass |
| 2026-07-27 | 328  | Wired Step-3 auto-append (`scripts/inventory_append.py` + `services/inventory_service.py` fire-and-forget on ship completion). Retroactive backfill of Iter 309/318/319/322/326/327. Ripple #2 verified (no template change). Ripple #4 (loopApi) noted as known risk. ShipConfirmModal→ShipPendingCard drift recorded. |


## 🔁 Ripple appends (auto-generated by scripts/inventory_append.py)


### Iter 309 · 2026-07-27

| `routers/dev_sse_probe.py` | `/aurem-dev/_iter309_probe` | 3 | Iter 309 · test-only synthetic SSE probe (reconnect harness, gated by AUREM_ENABLE_SSE_PROBE=1, 404s in prod) (Iter 309, 2026-07-27T08:38:33.006140Z) | <!-- inv:router:routers/dev_sse_probe.py -->
- `AUREM_ENABLE_SSE_PROBE` — Iter 309 · gate dev_sse_probe router (prod OFF by default) (default: 0) [Iter 309, 2026-07-27T08:38:33.006170Z] <!-- inv:envvar:AUREM_ENABLE_SSE_PROBE -->

### Iter 318 · 2026-07-27

- `services/loop_integrity_guard.py` — Iter 318 · pre-ship data-loss prevention (>70% size shrink block, placeholder ban, elision ban) — imported into loop_engine.py · status=wired [Iter 318, 2026-07-27T08:38:33.039593Z] <!-- inv:service:services/loop_integrity_guard.py -->
- `loop_run_log kind='integrity_guard_rejected'` — Iter 318 · fired when pre-ship integrity guard halts a ship at FAILED with a rule name + offending path (was: generic verify-fail with no reason) [Iter 318, 2026-07-27T08:38:33.039622Z] <!-- inv:loop_run_log_kind:integrity_guard_rejected -->

### Iter 319 · 2026-07-27

- `loop_run_log kind='scan_exception'` — Iter 319 · fail-CLOSED scan-phase NameError capture (was silently skipped; now blocks ship + records path) [Iter 319, 2026-07-27T08:38:33.071649Z] <!-- inv:loop_run_log_kind:scan_exception -->

### Iter 322 · 2026-07-27

- `loop_run_log kind='plan_latency_profile'` — Iter 322 · per-segment plan-phase latency profile (graph refresh vs repo map read vs LLM call) — RCA for loop_678eea28436c4e 21.6s incident [Iter 322, 2026-07-27T08:38:33.104590Z] <!-- inv:loop_run_log_kind:plan_latency_profile -->

### Iter 326 · 2026-07-27

- `services/integration_health.py` — Iter 326 A · Tavily 432 reclassify (broken→warn, credits-exhausted is soft top-up prompt, not outage). Iter 326 B · Stripe probe now retrieves each of 6 STRIPE_*_PRICE_ID and verifies .type==recurring (was: presence-check only, monthly one_time priceIDs silently passed). Iter 327 · Firecrawl probe adds prod-only WARN diagnostic logging (status + latency + resp head; no behavior change). · status=updated [Iter 326-327, 2026-07-27T08:38:33.137424Z] <!-- inv:service:services/integration_health.py -->

### Iter 0 · 2026-07-27

- `AUREM_ITER_NUM` — auto-detected in backend/main.py (verify) (default: unset) [Iter 0, 2026-07-27T16:41:09.934338Z] <!-- inv:envvar:AUREM_ITER_NUM -->
- `services/inventory_service.py` — auto-detected new service (verify) · status=wired [Iter 0, 2026-07-27T16:41:09.934365Z] <!-- inv:service:services/inventory_service.py -->
- `loop_run_log kind='router'` — auto-detected in backend/services/inventory_service.py (verify) [Iter 0, 2026-07-27T16:41:09.934377Z] <!-- inv:loop_run_log_kind:router -->
- `loop_run_log kind='service'` — auto-detected in backend/services/inventory_service.py (verify) [Iter 0, 2026-07-27T16:41:09.934389Z] <!-- inv:loop_run_log_kind:service -->
- `loop_run_log kind='envvar'` — auto-detected in backend/services/inventory_service.py (verify) [Iter 0, 2026-07-27T16:41:09.934401Z] <!-- inv:loop_run_log_kind:envvar -->
- `loop_run_log kind='loop_run_log_kind'` — auto-detected in backend/services/inventory_service.py (verify) [Iter 0, 2026-07-27T16:41:09.934412Z] <!-- inv:loop_run_log_kind:loop_run_log_kind -->
- `BRAND_NEW_ENV_VAR` — auto-detected in backend/tests/test_inventory_service.py (verify) (default: unset) [Iter 0, 2026-07-27T16:41:09.934420Z] <!-- inv:envvar:BRAND_NEW_ENV_VAR -->
- `loop_run_log kind='iter328_new_kind'` — auto-detected in backend/tests/test_inventory_service.py (verify) [Iter 0, 2026-07-27T16:41:09.934433Z] <!-- inv:loop_run_log_kind:iter328_new_kind -->

### Iter 328 · 2026-07-27

- `services/loop_ship_diff.py` — Iter 328 · Deploy 2 · compute_files_diff — per-file line/byte diff for ShipPendingCard safety pill · status=wired [Iter 328, 2026-07-27T18:41:30.225558Z] <!-- inv:service:services/loop_ship_diff.py -->
- `services/loop_rollback.py` — Iter 329 · real GitHub revert of a shipped loop commit via github_api_writer.revert_commit (non-force-push); persistence to loop_sessions.rollback_* · status=wired-dark [Iter 328, 2026-07-27T18:41:30.225570Z] <!-- inv:service:services/loop_rollback.py -->
- `services/integration_health_cron.py` — Iter 328 · #5 · periodic integration_health probe (INTEGRATION_HEALTH_INTERVAL_SEC default 600s) — writes latest + history; env ENABLE_INTEGRATION_HEALTH_CRON default ON · status=wired [Iter 328, 2026-07-27T18:41:30.225580Z] <!-- inv:service:services/integration_health_cron.py -->
- `ENABLE_INTEGRATION_HEALTH_CRON` — Iter 328 · #5 · gate periodic integration_health cron (default: 1) [Iter 328, 2026-07-27T18:41:30.225590Z] <!-- inv:envvar:ENABLE_INTEGRATION_HEALTH_CRON -->
- `INTEGRATION_HEALTH_INTERVAL_SEC` — Iter 328 · #5 · seconds between periodic integration_health probes (min 60, default 600) (default: 600) [Iter 328, 2026-07-27T18:41:30.225600Z] <!-- inv:envvar:INTEGRATION_HEALTH_INTERVAL_SEC -->
- `loop_run_log kind='loop_rollback_step'` — Iter 329 · loop_rollback background worker step audit trail [Iter 328, 2026-07-27T18:41:30.225608Z] <!-- inv:loop_run_log_kind:loop_rollback_step -->

---

## 🔍 Tier 3 discrepancy verification (Iter 328 · #12)

History claimed these shipped ~Iter 114-115. Live-codebase evidence
(Iter 328):

| Feature | Verdict | Evidence |
|---|---|---|
| Loop Readiness Score (`/loop/audit`) | **NOT-FOUND** | Zero backend refs for `loop_readiness`, `/loop/audit`, `readiness_score` |
| Pattern templates (Daily Triage / Dep Sweeper / Changelog Drafter) | **NOT-FOUND** | Zero backend refs |
| Branch-per-fix mode | **EXISTS-ORPHANED** | Only a User-Agent string in `services/finding_fix_applier.py:54`; no `aurem-fix/*` branch code, no draft-PR toggle, no settings gate |
| L1/L2/L3 trust levels | **EXISTS-WIRED** | `loop_engine.py:681-703` reads trust level; enforcement at L1 block gate + L3 auto-ship (line 2682) |

- `FEATURE_FLAG_integration_health_cron` — Iter 328 · #11 · runtime pause of integration_health_cron via /admin/feature-flags. Env ENABLE_INTEGRATION_HEALTH_CRON is boot-time gate; feature flag is runtime kill-switch. Both must allow for probes to fire. (default: true (seeded in feature_flags collection)) [Iter 328, 2026-07-27T19:21:53.886060Z] <!-- inv:envvar:FEATURE_FLAG_integration_health_cron -->
- `services/project_brain.py` — Iter 328 · #3-a · fail-open silent-failure logging on update_brain_after_commit (visible at WARNING when writes fail). Zero callsites in prod code still — step (b) reattachment held for founder-guided decisions. · status=instrumented-dead [Iter 328, 2026-07-27T19:50:41.865151Z] <!-- inv:service:services/project_brain.py -->
- `backend narration correlation_id gap` — Iter 329 · Fix B tracking note · On some terminal transitions (confirmed live on ship-success commit 1f70444, also affects abort/expired paths) the loop_engine.py phase code emits an INITIAL pending narration but NEVER a correlation_id-matching resolver frame. Fix B (LoopLiveFeed.jsx resolvePendingOnTerminal) makes the RENDER LAYER resilient — pending lines auto-resolve to success/warning/danger tone on terminal. The BACKEND CAUSE is still open: audit every phase in loop_engine.py for narration frames without matching resolver emit + close SSE gap window on terminal. · status=backend-cause-open-ui-mitigated [Iter 329, 2026-07-27]

### Iter 0 · 2026-07-28

- `ORA_CANARY_ENABLED` — auto-detected in backend/routers/admin.py (verify) (default: unset) [Iter 0, 2026-07-28T06:29:25.601081Z] <!-- inv:envvar:ORA_CANARY_ENABLED -->
- `ENABLE_EVAL_CRON` — auto-detected in backend/routers/admin.py (verify) (default: unset) [Iter 0, 2026-07-28T06:29:25.601105Z] <!-- inv:envvar:ENABLE_EVAL_CRON -->
- `ORA_LEARNING_DISABLED` — auto-detected in backend/routers/admin.py (verify) (default: unset) [Iter 0, 2026-07-28T06:29:25.601117Z] <!-- inv:envvar:ORA_LEARNING_DISABLED -->
- `services/tool_executor.py` — auto-detected new service (verify) · status=wired [Iter 0, 2026-07-28T06:29:25.601127Z] <!-- inv:service:services/tool_executor.py -->
- `loop_run_log kind='skipped_at_ship'` — auto-detected in backend/services/loop_engine.py (verify) [Iter 0, 2026-07-28T17:41:08.528452Z] <!-- inv:loop_run_log_kind:skipped_at_ship -->
