# 🗺️ AUREM CODEBASE — Full Structural Map

**Generated:** 2026-07-17 (Iter 212m-252)  ·  **Auto-computed from live index**  ·  **Source of truth:** this file + files under `/app/memory/architecture/`

---

## 📊 Repo scale (live counts)

| Layer | Count | Path |
|---|---|---|
| Backend routers | **55** | `backend/routers/*.py` |
| Backend services | **108** | `backend/services/*.py` |
| ORA Chat subpackage | **9** modules | `backend/services/ora_chat/` |
| Backend core (Parliament) | **6** | `backend/core/*.py` |
| Backend templates (stacks) | **30** files across **4** stacks | `backend/templates/stacks/` |
| Backend tests | **330** files | `backend/tests/` |
| Frontend pages (root) | **42** | `frontend/src/pages/*.jsx` |
| Frontend pages — personal/ | **7** | `frontend/src/pages/personal/` |
| Frontend pages — admin/ | **2** | `frontend/src/pages/admin/` |
| Frontend components (non-ui) | **81** | `frontend/src/components/*.jsx` |
| Shadcn UI components | **~40** | `frontend/src/components/ui/` |
| **Total indexed source files** | **803** | `/app/backend` + `/app/frontend/src` |

---

## 🏛️ HIGH-LEVEL ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────┐
│                   USER (Browser / PWA)                       │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS (auremcto.com prod / preview.emergentagent.com dev)
┌──────────────────────────▼──────────────────────────────────┐
│         FRONTEND — React SPA (Port 3000, Vite/CRA)           │
│   42 root pages · 7 personal · 2 admin · 81 components       │
│   Tailwind + Shadcn UI + Context API + SSE streaming         │
└──────────────────────────┬──────────────────────────────────┘
                           │ /api/* (Kubernetes ingress)
┌──────────────────────────▼──────────────────────────────────┐
│              BACKEND — FastAPI (Port 8001)                   │
│   55 Routers → Parliament Core → 108 Services → External     │
│   Entry: backend/main.py (all include_router calls)          │
└──────┬───────────┬───────────┬───────────┬──────────────────┘
       │           │           │           │
   ┌───▼───┐  ┌────▼─────┐ ┌───▼────┐ ┌────▼──────────────┐
   │MongoDB│  │GitHub API│ │ Stripe │ │ LLM Providers     │
   │(Motor)│  │(PAT/OAuth│ │Payments│ │ • OpenRouter      │
   └───────┘  └──────────┘ └────────┘ │ • Anthropic (Emrg)│
                                       │ • DeepSeek/Sonar  │
                                       │ • Perplexity      │
                                       │ • GLM             │
                                       └───────────────────┘
```

---

## 🧩 SUBSYSTEM MAP (10 crown-jewel subsystems, in engineering weight order)

### 1️⃣ Council + Loop Engine — Multi-agent orchestration
- **Routers:** `loop.py`, `chat.py` (Council branch)
- **Services:** `loop_engine.py`, `loop_execute.py`, `loop_full_scan.py`, `loop_safety.py`, `loop_verify.py`, `mode_b_council.py`, `mode_d_debugger.py`, `mode_e_auditor.py`, `mode_f_engage.py`
- **Core:** `core/parliament.py`, `core/intent_gateway.py`, `core/tool_router.py`
- **Frontend:** `Dashboard.jsx`, `admin/AdminParliamentLive.jsx`

### 2️⃣ Personal Track (T0–T4) — Non-technical user app-generation
- **Router:** `personal_track.py`
- **Services:** `personal_track_quotas.py`, `personal_track_smoke.py`, `preview_sandbox.py`, `scaffold_llm.py`
- **Frontend:** `frontend/src/pages/personal/` (7 pages — ChooseTrack → BuildHome → DraftReview → PreviewPanel → ShipProgress → BuildSuccess + _shell)
- **Templates:** `backend/templates/stacks/{react-fastapi,nextjs-node,vue-express,plain-html}/` (30 boilerplate files)
- **Admin:** `admin/PersonalTrackAdmin.jsx`, `components/PersonalTrackBanner.jsx`

### 3️⃣ Feature Window + Security Gate — Spec-driven feature builds
- **Router:** `feature_window.py`
- **Services:** `full_scan_orchestrator.py`, `full_scan_scanners.py`, `finding_fix_applier.py`, `fix_pipeline.py`, `fix_triage.py`
- **Frontend:** `FeatureWindow.jsx`

### 4️⃣ ORA Chat — Founder-only multi-model chat (PIN-gated)
- **Router:** `ora_chat.py` (SSE streaming `/message`, `/slash`, `/pin-login`, sessions)
- **Subpackage:** `backend/services/ora_chat/` — **9 modules:**
  - `router.py` — route configs (general/research/deep/slash/slash_explain/tool_orchestration)
  - `safety.py` — CORE_SAFETY_RULES + AUREM_CONTEXT + assemble_system_prompt + KNOWN_COMMANDS
  - `providers.py` — OpenRouter wrapper (one_shot + stream)
  - `session.py` — persistent MongoDB session storage
  - `cost_tracker.py` — daily/monthly budget guardrails
  - `house_rules.py` — admin custom preferences
  - `slash_commands.py` — 11 deterministic commands (5 DB + 5 codebase + help)
  - `deep_research.py` — multi-label classifier + parallel fan-out orchestrator
  - `codebase_index.py` — 803-file BM25-lite retrieval + system_highlights + path-traversal-safe read
- **Frontend:** `OraDirect.jsx` (standalone `/ora` route), `OraChatDrawer.jsx`, `OraChatHouseRulesPanel.jsx`, `admin/OraChat.jsx`
- **Tests:** `test_ora_chat.py` (47), `test_ora_chat_deep_research.py` (24), `test_ora_chat_codebase.py` (25), `test_ora_chat_pin_login.py` (7) = **104/104 green**

### 5️⃣ Stripe Billing — Subscriptions + credits + reconciliation
- **Router:** `payments.py`
- **Services:** `stripe_client.py`, `billing_cron.py`, `usage.py`, `subscription_tiers.py`, `scan_fix_quota.py`
- **Frontend:** `Pricing.jsx`, `Settings.jsx`

### 6️⃣ Ask Advisor — GLM/Claude "explain my code" assistant
- **Router:** `chat.py` (Ask Advisor branch), `advisor_context.py`
- **Services:** `advisor_vision.py`, `llm_router.py`, `llm.py`
- **Frontend:** `Dashboard.jsx` (advisor panel)

### 7️⃣ Codebase Health / Bug Hunt / Findings pipeline
- **Routers:** `codebase_health.py`, `security_scan.py`, `harden.py`, `findings.py`, `vanguard_ci.py`, `admin_vanguard.py`, `fix_pipeline.py`
- **Services:** `full_scan_orchestrator.py`, `bug_hunt_rules.py`, `fixed_findings.py`, `citation_guard.py`, `hallucination_guard.py`, `architecture_health.py`, `codebase_indexer.py`, `design_linter.py`, `vanguard_scanner.py`
- **Frontend:** `CodebaseHealth.jsx`, `BugHunt.jsx`, `AdminVanguard.jsx`

### 8️⃣ GitHub + Vercel + Supabase + Managed DB — Deploy pipeline
- **Routers:** `github_oauth.py`, `github_bot.py`, `github_deploy.py`, `hosted_deploy.py`, `deploy.py`, `vercel.py`, `supabase.py`, `managed_db.py`, `domain.py`, `stacks.py`
- **Services:** `github_org_client.py`, `github_api_writer.py`, `github_cache.py`, `github_deploy_service.py`, `github_issues_context.py`, `github_oauth.py`, `supabase_provisioner.py`, `aurem_managed_db.py`
- **Frontend:** `Deploy.jsx`, `Domain.jsx`, `Integrations.jsx`, `GitHubCard.jsx`

### 9️⃣ Admin Panel — Ops control surface
- **Routers:** `admin.py`, `admin_bin.py`, `admin_vanguard.py`, `mfa.py`
- **Services:** `admin_analytics_cache.py`, `feature_flags.py`, `audit_log.py`, `financials.py`, `bin_context.py`, `mfa.py`
- **Frontend:** 12 `Admin*.jsx` root pages + `admin/PersonalTrackAdmin.jsx` + `admin/OraChat.jsx` + `AuremAdminPanel.jsx` + `AdminHouseRules.jsx` + `AdminRobotGuide.jsx` + `AdminThinkingHints.jsx`

### 🔟 Codebase Indexer (Ora's own repo awareness)
- **Services:** `services/ora_chat/codebase_index.py` (fresh, Iter 212m-246) + `services/codebase_indexer.py` (legacy, general-purpose)
- **Repo scan roots:** `/app/backend`, `/app/frontend/src`
- **Skip dirs:** `node_modules`, `.git`, `__pycache__`, `.pytest_cache`, `build`, `dist`, `.next`, `.venv`, `venv`

---

## 🗂️ FRONTEND — All 51 Pages (grouped)

### Public / Marketing
`Landing.jsx`, `Pricing.jsx`, `Demo.jsx`, `VsDevin.jsx`, `WhyOra.jsx`, `PolicyPage.jsx`

### Auth
`Login.jsx`, `Signup.jsx`, `OAuthFinish.jsx`

### Dashboard / Chat
`Dashboard.jsx`, `DashboardPreviewV2.jsx`, `BrainDump.jsx`, `Projects.jsx`

### Product features
`FeatureWindow.jsx`, `CodebaseHealth.jsx`, `BugHunt.jsx`, `Automations.jsx`, `Analytics.jsx`, `Wrapped.jsx`, `ShipWall.jsx`, `Tokens.jsx`, `ToolsPage.jsx`, `SidebarPreview.jsx`, `OpsRecipes.jsx`, `SystemStatsPage.jsx`

### Deploy / Integrations
`Deploy.jsx`, `Domain.jsx`, `Integrations.jsx`

### ORA
`OraDirect.jsx` (standalone `/ora` route with plaster background)

### Settings
`Settings.jsx`

### Admin (12 root)
`Admin.jsx`, `AdminOverview.jsx`, `AdminSystemHealth.jsx`, `AdminFinancials.jsx`, `AdminBINTracker.jsx`, `AdminApiKeys.jsx`, `AdminLLMCredits.jsx`, `AdminFeatureFlags.jsx`, `AdminIntegrations.jsx`, `AdminSuggestions.jsx`, `AdminParliamentLive.jsx`, `AdminVanguard.jsx`

### Personal Track (`/personal/*` — 7 pages)
`_shell.jsx`, `ChooseTrack.jsx`, `BuildHome.jsx`, `DraftReview.jsx`, `PreviewPanel.jsx`, `ShipProgress.jsx`, `BuildSuccess.jsx`

### Admin nested (2)
`admin/PersonalTrackAdmin.jsx`, `admin/OraChat.jsx`

---

## 🔌 BACKEND — All 55 Routers (grouped by responsibility)

### Auth & Identity (5)
`auth.py` · `oauth.py` · `github_oauth.py` · `mfa.py` · `onboarding.py`

### Chat / AI (5)
`chat.py` · `chat_commits.py` · `loop.py` · `diagram.py` · `thinking_hints.py`

### ORA Chat (1)
`ora_chat.py` — SSE streaming, PIN gate, 11 slash-commands, deep-research, codebase awareness

### Scanning (5)
`codebase_health.py` · `security_scan.py` · `lint_preview.py` · `vanguard_ci.py` · `harden.py`

### Fixing / Findings (2)
`fix_pipeline.py` · `findings.py`

### Repo / GitHub (5)
`cto_projects.py` · `repo_indexing.py` · `repo_status.py` · `github_bot.py` · `mcp.py`

### Deploy (6)
`deploy.py` · `vercel.py` · `hosted_deploy.py` · `github_deploy.py` · `domain.py` · `stacks.py`

### Business / Billing (12)
`payments.py` · `usage.py` · `founder_offer.py` · `unlock.py` · `feature_window.py` · `engagement.py` · `notify_interest.py` · `trust.py` · `trust_level.py` · `shipwall.py` · `wrapped.py` · `suggestions.py`

### Personal Track (3)
`personal_track.py` · `scaffold.py` · `supabase.py`

### Managed DB (1)
`managed_db.py`

### Admin (3)
`admin.py` · `admin_bin.py` · `admin_vanguard.py`

### Ops / Support (5)
`automations.py` · `support.py` · `upload.py` · `vault.py` · `qa_probe.py`

### Version / Misc (2)
`version.py` · `advisor_context.py`

---

## ⚙️ BACKEND — Services taxonomy (108 modules, grouped)

### 🧠 AI / LLM plumbing (12)
`llm.py`, `llm_router.py`, `llm_file_parser.py`, `agents.py`, `code_reviewer.py`, `advisor_vision.py`, `error_translator.py`, `mode_classifier.py`, `mode_b_council.py`, `mode_d_debugger.py`, `mode_e_auditor.py`, `mode_f_engage.py`

### 🔒 Safety / Guards (5)
`hallucination_guard.py`, `citation_guard.py`, `loop_safety.py`, `bug_hunt_rules.py`, `house_rules.py`

### 🔁 Loop engine (5)
`loop_engine.py`, `loop_execute.py`, `loop_full_scan.py`, `loop_safety.py`, `loop_verify.py`

### 🔍 Scanning / Findings (10)
`full_scan_orchestrator.py`, `full_scan_scanners.py`, `fix_job_manager.py`, `fix_triage.py`, `finding_fix_applier.py`, `fixed_findings.py`, `vanguard_scanner.py`, `architecture_health.py`, `design_linter.py`, `codebase_indexer.py`

### 💳 Billing / Usage (5)
`billing_cron.py`, `stripe_client.py`, `usage.py`, `subscription_tiers.py`, `scan_fix_quota.py`

### 📦 Repo / GitHub / Deploy (10)
`github_api_writer.py`, `github_cache.py`, `github_deploy_service.py`, `github_issues_context.py`, `github_oauth.py`, `github_org_client.py`, `git_identity.py`, `local_tools.py`, `file_selector.py`, `graph_builder.py`

### 🚀 Personal Track scaffolding (4)
`personal_track_quotas.py`, `personal_track_smoke.py`, `preview_sandbox.py`, `scaffold_llm.py`

### 🗄️ Managed DB / Supabase (2)
`aurem_managed_db.py`, `supabase_provisioner.py`

### 🔔 Notifications / Ops (7)
`onboarding_email.py`, `daily_digest.py`, `db_backup.py`, `deploy_logger.py`, `integration_health.py`, `audit_log.py`, `langfuse_tracing.py`

### 📊 Admin / Analytics (5)
`admin_analytics_cache.py`, `feature_flags.py`, `financials.py`, `bin_context.py`, `app_state.py`

### 🌐 External / Registry (3)
`external_services_registry.py`, `mcp_scoped_tools.py`, `dev_skills.py`

### 🎨 Other utility (~40)
`mermaid_diagram.py`, `generation_rules.py`, `mfa.py`, `chat_service.py`, ... (see `ls backend/services/*.py` for full list)

### 💬 ORA Chat (subpackage — 9 modules)
See section 4️⃣ above.

---

## 🧪 TEST COVERAGE — 330 files under `backend/tests/`

Key modules (recently added):
- ORA Chat suite: **4 files, 104 tests** (test_ora_chat.py, deep_research, codebase, pin_login)
- Personal Track: `test_iter212m235_phase6_*.py`, `test_iter212m235_track_field.py`
- Security gate: `test_iter212m237_security_gate.py`
- Tier billing: `test_iter212m240_tier3_tier4_billing_gate.py`
- Supabase provisioning: `test_iter212m234_phase5_*.py`
- Legacy iterations: 300+ files covering every past iter (212m-{1..250})

Run all ORA suite: `cd /app/backend && python -m pytest tests/test_ora_chat*.py -q`

---

## 🗃️ DATA MODEL (MongoDB, key collections)

| Collection | Purpose |
|---|---|
| `dev_users` | Auth + tier + track + stripe_customer_id + is_founder + personal_nudge_clicked_at |
| `cto_projects` | Connected repos (github_token stored encrypted) |
| `cto_tasks` | Task ledger (1 fix = 1 task) |
| `cto_fixed_findings` | Applied-fix ledger (hidden from rescan until PR merges) |
| `ora_chat_sessions` | Persistent ORA Chat sessions |
| `ora_chat_usage` | Per-message cost/token/model log |
| `ora_chat_house_rules` | Admin custom preferences |
| `ora_chat_pin_attempts` | Rate-limit (5 wrong / hour) |
| `ora_council_logs` | Multi-agent transcript (Ask Advisor + Council) |
| `personal_track_*` | T0-T4 stage progress |
| `subscriptions` | Stripe subscription cache |
| `payment_events` | Stripe webhook ledger |
| `quality_scores` | Response quality metrics |
| `audit_log` | Admin action trail |

---

## 🌐 EXTERNAL INTEGRATIONS

| Service | Purpose | Key/Auth |
|---|---|---|
| **OpenRouter** | DeepSeek V3/R1, GLM, Perplexity Sonar routing | `OPENROUTER_API_KEY` |
| **Anthropic (Emergent)** | Claude Sonnet 4.5 (Council primary) | `EMERGENT_LLM_KEY` (universal) |
| **Anthropic direct (future)** | Claude Haiku 4.5 native web_search+web_fetch | `ANTHROPIC_API_KEY` (not set yet) |
| **Stripe** | Subscriptions + credits | `STRIPE_API_KEY` |
| **GitHub REST + OAuth** | Repo access + PRs + org PAT | `GITHUB_CLIENT_ID/SECRET`, per-user PAT |
| **Vercel** | Personal Track deploys | `VERCEL_API_TOKEN` |
| **Supabase** | User-provisioned Postgres | User's `SUPABASE_ACCESS_TOKEN` |
| **Sentry** | Error tracking | `SENTRY_DSN` |
| **Meta Pixel** | Marketing analytics | frontend-only |
| **Google Auth** | Social login | `GOOGLE_CLIENT_ID/SECRET` |
| **Reddit JSON** | ORA social search (unauthenticated) | none |
| **GDELT** | ORA news search | none |
| **MongoDB Motor** | Async driver | `MONGO_URL`, `DB_NAME` |

---

## 🚦 THE 5 CORE DESIGN PATTERNS (unchanged)

1. **Parliament Pattern** — every AI request → `core/intent_gateway.py` → `core/parliament.py` → specialized agent. No endpoint calls an agent directly.
2. **Fixed-Findings Ledger** — applied fix recorded in `cto_fixed_findings` and hidden from rescans until PR merges.
3. **Quota-as-Tasks** — 1 fix = 1 task, deducted only on success via `record_scan_fixes()`.
4. **Local Snapshot Cache** — `.aurem_cache` git tree so scans avoid GitHub rate limits.
5. **Guard Layers** — `hallucination_guard.py` + `citation_guard.py` must BOTH pass before any fix touches a repo.

Additional pattern added in this session (Iter 212m-246):

6. **Codebase Awareness Layer** — Ora chat auto-injects a compact repo tree + curated system_highlights into every message. BM25-lite retrieval with min_tokens=2 + min_score=3.5 threshold. Meta questions bypass retrieval and answer from system_highlights (avoids hallucination on generic queries).

---

## 🔗 File-to-URL routing (frontend)

| URL path | Component |
|---|---|
| `/` | `Landing.jsx` |
| `/login`, `/signup` | `Login.jsx`, `Signup.jsx` |
| `/dashboard` | `Dashboard.jsx` |
| `/ora` | `OraDirect.jsx` (PIN 7668 gate) |
| `/feature-window` | `FeatureWindow.jsx` |
| `/deploy` | `Deploy.jsx` |
| `/personal/*` | `personal/_shell.jsx` → nested T0-T4 pages |
| `/admin/*` | `Admin.jsx` → nested admin pages |
| `/admin/ora-chat` | `admin/OraChat.jsx` (drawer view) |
| `/admin/personal-track` | `admin/PersonalTrackAdmin.jsx` |
| `/settings`, `/pricing`, `/analytics`, `/wrapped` | respective root pages |

All non-`/api/*` paths → frontend (port 3000). All `/api/*` → backend (port 8001).

---

## 🌀 Deploy environments

| Env | URL | Access | Deploy trigger |
|---|---|---|---|
| **Preview (dev)** | `launch-pad-237.preview.emergentagent.com` | Agent-editable | Auto on every commit |
| **Production** | `auremcto.com` | User-only redeploy | Manual "Deploy" button |

Environment variables live in `backend/.env` and `frontend/.env` per environment. Never hardcode URLs.

---

## 📌 What changed in the last 24 hrs (Iter 212m-245 → 251)

| Iter | Change | Files |
|---|---|---|
| 245 | Auto Deep-Research (multi-source parallel + synth) | +`deep_research.py`, +24 tests |
| 246 | Codebase Awareness + wider input + thinking dots + Stop | +`codebase_index.py` (494 LOC), +25 tests, `OraDirect.jsx`, `OraChatDrawer.jsx` |
| 247 | Message-list padding fix | `OraDirect.jsx` (1-line) |
| 248 | Production PIN 503 fix | `routers/ora_chat.py`, +7 tests |
| 249 | Meta-question hallucination fix | `codebase_index.py` (stopwords + threshold + system_highlights), `deep_research.py` (classifier prompt) |
| 250 | Plaster background image | `OraDirect.jsx` |
| 251 | Chat width 50% → 40% | `OraDirect.jsx` (1-line) |
| 252 | THIS DOC — codebase mapping refresh | `memory/CODEBASE_MAP.md` (NEW), `memory/architecture/{01,02,03,04}.md` counts refresh |

See `/app/memory/PRD.md` for the fuller history (12,961 lines, 250+ iterations).

---

## 🔍 How to explore (for ORA + humans)

**Slash commands in ORA Chat:**
- `/repo-tree` — compact directory listing
- `/repo-stats` — file/lang/def counts
- `/find <pattern>` — glob/substring path match
- `/read <path>` — bounded file read (200 lines / 40 KB)
- `/defs <symbol>` — locate function/class definition
- `/users-today`, `/active-users`, `/revenue-snapshot`, etc. — DB metrics

**Shell one-liners:**
```bash
# Every router file
ls /app/backend/routers/*.py | grep -v __

# Every service file
ls /app/backend/services/*.py | grep -v __

# Rebuild codebase index programmatically
cd /app/backend && python -c "import asyncio; from services.ora_chat import codebase_index as cb; \
  print(asyncio.run(cb.build_index(force=True)))"
```

---

*This document is the single source of truth for the current codebase state. Regenerate any time the count of files changes materially — for now, this Iter 212m-252 snapshot is fresh. See files 02-07 under `/app/memory/architecture/` for deeper per-layer detail.*
