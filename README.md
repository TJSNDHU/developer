<div align="center">

![AUREM CTO](./docs/banner.svg)

# ORA by Aurem CTO
**developer's choice · by Aurem CTO**

*AI engineer that reads your GitHub repo, writes production-ready code, runs security checks, and commits directly. No IDE. No PR juggling. Flat pricing.*

[**Start free**](https://auremcto.com/signup) · [**Live product**](https://auremcto.com) · [**Ship Wall**](https://auremcto.com/wall) · [**Pricing**](https://auremcto.com/pricing)

[![Pro plan](https://img.shields.io/badge/Pro-$19%2Fmo_flat-f59e0b?style=for-the-badge)](https://auremcto.com/pricing)
[![Modes](https://img.shields.io/badge/Modes-Swift_·_Pro_·_Maxx-0ea5e9?style=for-the-badge)](#agent-system)
[![MCP](https://img.shields.io/badge/MCP-2025--03--26-22c55e?style=for-the-badge)](#mcp-integration-claude-desktop--clis)

</div>

---

## What ORA does

You describe a change in plain language. ORA reads your repo via the GitHub API, plans the patch, writes the code, runs a security pass (Vanguard 007 regex catalog + Claude review when relevant), and pushes a real commit to your branch — typically in **6–30 s** for a focused change.

The whole loop is exposed through:

- **Web chat** (`/dashboard`) — the primary surface.
- **MCP server** (`/api/aurem-dev/mcp`, JSON-RPC 2.0, spec `2025-03-26`) — Claude Desktop / Claude Code / Cursor / any MCP client.
- **VS Code extension** (`vscode-extension/`, v0.2.0) — same backend, status bar control.
- **OAuth 2.1 + PKCE flow** (`/oauth/authorize`) — for hosted IDE integrations that need a per-user token without sharing API keys.

There is no IDE plug-in to install for the core flow. Code goes from chat to commit in one turn.

---

## Architecture

```
┌──────────────────────────┐     ┌──────────────────────────────┐
│  React 19 + Vite (3000)  │ ──▶ │  FastAPI on :8001            │
│  · ChatPanel             │     │  · 30 routers · 188 endpoints│
│  · ORASidePanel (Ask)    │     │  · Motor (async Mongo)       │
│  · LiveTaskPopup / Tape  │     │                              │
│  · PricingCards + Stripe │ ◀── │  Background worker queue     │
└──────────────────────────┘ SSE └──────────────────────────────┘
                                          │
                                          ▼
                            ┌──────────────────────────┐
                            │  OpenRouter (LLM gateway)│
                            │  Kimi K2 · K2.5 · K2.7   │
                            │  Claude Sonnet/Opus 4.5  │
                            │  DeepSeek (fallback)     │
                            └──────────────────────────┘
                                          │
                                          ▼
                            ┌──────────────────────────┐
                            │  GitHub REST API         │
                            │  (PAT or OAuth token)    │
                            └──────────────────────────┘
```

- **Backend**: FastAPI · Motor · MongoDB · 364 source files
- **Frontend**: React 19 · Vite · 26 pages · 37 components
- **LLM gateway**: OpenRouter exclusively (no direct OpenAI/Anthropic SDK calls — see `services/llm.py::call_openrouter_model`)
- **Hosting**: Emergent platform (`auremcto.com`)
- **Tests**: pytest suite under `backend/tests/` (iter 179, 181, 182, 183 regression files included)

---

## Features (every section corresponds to real code)

### Chat & coding
- **Three review modes** — Swift / Pro / Maxx, tier-gated via `/chat/modes/available` (`ModeSelector.jsx`)
- **Mode classifier** — auto-routes chat vs. ship vs. council requests (`services/mode_classifier.py`)
- **Parallel agents** for multi-file tasks (`services/parallel_agents.py`)
- **Project Brain** — per-project memory written/read at the start of every chat (`services/project_brain.py`)
- **Knowledge Graph** — code symbol graph per project, rendered in `GraphPanel.jsx` (`services/graph_builder.py`)
- **Codebase indexer** — semantic file index for repo-aware retrieval (`services/codebase_indexer.py`)
- **Warm Start** — pre-fetched repo + Brain + Graph in <1 s on session resume (`WarmStatusBar.jsx`, `warm_start_jobs` TTL index)
- **Live task popup** — floating SSE-driven progress bar (`LiveTaskPopup.jsx`, `TaskLiveTape.jsx`)
  · iter 212m-10: synthetic `task_handoff` frame on fast-finish so the popup latches on for 1-2 s ships
- **Ship Wall** — public feed of completed tasks at `/wall` (`pages/ShipWall.jsx`)
- **F12 error capture** — browser errors auto-attached to next chat turn (`ChatPanelF12.jsx`)

### Deploy (BYOH — Bring-Your-Own-Host)
- **Per-project SSH config** with hybrid fallback to a user-level default (iter 212m-9, `routers/deploy.py`)
- **DeployPanel** — 4-state UI (no_config / idle / deploying / done|failed) inside the Preview side panel (`components/DeployPanel.jsx`)
- **One-click actions** — `Deploy now`, `Dry run` (auth + compose validation, no restart), `Rollback`
- **Live log tail** — polled SSE-style cursor at `GET /deploy/runs/{run_id}/logs?since=N`
- **"Code shipped — ready to go live?"** reminder banner on every completed Ship-via-CTO task (`ShipDialog.jsx`)
- **Encrypted SSH keys** at rest via `AUREM_CTO_MASTER_KEY` — never returned in the clear from the API

### Ask Advisor side panel (`ORASidePanel.jsx`)
- Slides in from the right on demand (35vw, 360–680px clamped)
- Voice in (`SpeechRecognition`) + voice out (`useTextToVoice`)
- Two-mode advisor prompt — **MODE 1: Technical Support · MODE 2: Advisory** (150-word ceiling, no "it depends")
- **2-step support escalation** — Advisor first → "Did this fix?" Yes/No → on No, drafts a context-loaded support email to **polarisbuiltinc@gmail.com** and opens `mailto:`

### GitHub
- **OAuth + PAT** dual-mode auth (`routers/github_oauth.py`, `services/github_api_writer.py`)
- **Atomic commits** through `gh_api_commit` — no local clone, no merge conflicts
- **Auto-deploy hooks** for Railway / Vercel (`services/github_deploy_service.py`, `routers/hosted_deploy.py`)
- **Repo context** — auto-fetched relevant files per turn (`services/repo_context.py`)
- **Save-to-GitHub dialog** for ad-hoc file commits (`SaveToGithubDialog.jsx`)

### Security (Vanguard 007)
- **Pre-commit secret scan** — 25+ regex patterns: AWS keys, GitHub tokens, Slack tokens, private keys, generic API tokens (`services/vanguard_scanner.py`)
- **Verify agent** — second-pass LLM audit of generated code before commit (`services/vanguard_verify_agent.py`)
- **Post-task scan** — regex security + import lint on every shipped file, fire-and-forget after commit (`services/post_task_scanner.py`, `PostTaskScan.jsx`)
- **Repo audit mode** — full static scan exposed via `Mode E Auditor` (`services/mode_e_auditor.py`)
- **Architecture health** baseline tracking (`services/architecture_health.py`)
- **False-positive hygiene** (iter 212m-11):
  - `openai_key` excludes `sk-aurem-*` / `sk-test-*` placeholders via negative lookahead
  - `requests_no_verify` scoped to real HTTP clients (`requests` / `httpx` / `urllib`) — no longer fires on `{"verify": False}` config dicts
  - `token_assignment` drops bare `token` (catches only `bearer`/`auth_token`/`access_token`/`refresh_token`), requires 16+ char literal
  - `generic_secret` raised to 16 chars, negative-lookbehind so `client_secret = os.getenv(...)` no longer fires
  - **Per-line opt-out** — append `# vanguard: ignore` (Py) or `// vanguard: ignore` (JS) to a line to suppress a single finding without whitelisting the whole file
  - **LLM verify agent** normalises Python `True/False/None` → JSON `true/false/null` before `json.loads()` so a single literal slip no longer drops the entire review (which previously defaulted to `pass=True` and silently missed findings)

### Subscriptions & monetization
- **Stripe Checkout** — subscription mode, monthly + annual price IDs, `/g/pay/` → `/c/pay/` URL rewrite (iter 183 fix)
- **Billing portal** for self-service plan changes (`/payments/portal`)
- **Referral rewards** — auto-grant on paid conversion (`services/billing_cron.py::grant_referral_reward`)
- **Founder unlimited tier** — token enforcement bypass via `FOUNDER_EMAILS` env var (`services/usage.py::is_founder_email`)

### Admin
- **Overview, Users, Tasks, Thinking Hints, API Keys, Financials, Integrations, Vanguard** — 8 admin pages (`pages/Admin*.jsx`)
- **Daily digest** email cron + `db_backup` cron (3 AM UTC, 7-day retention)

---

## API Endpoints (191 routes across 30 routers)

| Router | Routes | Purpose |
| :--- | :---: | :--- |
| `admin.py` | 57 | Admin panel — users, tasks, financials, integrations, vanguard |
| `cto_projects.py` | 20 | Project CRUD, task submit/stream/cancel, parallel agents |
| `chat.py` | 12 | Chat send/stream/history, sessions, support-email draft |
| `deploy.py` | 9 | BYOH SSH deploy — config (per-project + user-level), run, runs history, log tail |
| `shipwall.py` | 7 | Public ship feed |
| `thinking_hints.py` | 7 | UI loading hints |
| `mcp.py` | 6 | JSON-RPC MCP endpoint + API key management |
| `oauth.py` | 6 | OAuth 2.1 + PKCE for hosted IDE integration |
| `payments.py` | 6 | Stripe checkout, webhook, my-plan, billing portal |
| `automations.py` | 6 | Scheduled hooks |
| `github_deploy.py` | 6 | Railway/Vercel deploy bridge |
| `github_oauth.py` | 5 | GitHub OAuth dance + token storage |
| `trust.py` | 5 | Trust badge + verification |
| `auth.py` | 4 | Signup, login, me, logout |
| `projects.py` | 4 | Public project listing |
| `engagement.py` | 4 | Streaks / activity |
| `hosted_deploy.py` | 4 | One-click hosting widget |
| `chat_commits.py` | 3 | Save assistant code to GitHub |
| `domain.py` | 3 | Custom domain config |
| `support.py` | 3 | Support inbox |
| `usage.py` | 3 | Token wallet |
| Other 9 routers | 11 | wrapped, vault, unlock, harden, lint_preview, upload, stacks, github_bot |

**Base path**: `/api/aurem-dev` (all routes prefixed). Health: any of the documented sub-paths returns 200/4xx — there is no `/api/health` route.

---

## Agent System

Defined in `services/agents.py` and routed through `services/smart_router.py` (iter 165):

| Agent | Model (default) | Job |
| :--- | :--- | :--- |
| **ReaderAgent** | Kimi K2 (cheap) | Fetch the right files from the repo |
| **CoderAgent** | Kimi K2.7 Code (Swift/Pro) · Claude Sonnet 4.5 (Maxx) | Generate the patch |
| **ReviewerAgent** | Kimi K2.5 (Swift) · Kimi K2 Thinking (Pro) · Claude (Maxx) | Diff-review the patch |
| **SecurityAgent** | Always Claude | Secret / SQLi / XSS scan |
| **CoordinatorAgent** | n/a | Orchestrates the four above |

**Mode → cost** (estimated, input+output):

- Swift  ≈ **$0.040** / task
- Pro    ≈ **$0.045** / task
- Maxx   ≈ **$0.085** / task

Every model ID can be hot-swapped via env vars (`AUREM_MODEL_SWIFT_CODE`, etc.) without redeploying. Fallback on any error is `deepseek/deepseek-chat`.

Additional orchestration:
- **Mode B Council** — multi-LLM consensus (`services/mode_b_council.py`)
- **Mode D Debugger** — focused debugger agent (`services/mode_d_debugger.py`)
- **Mode E Auditor** — repo-wide security audit (`services/mode_e_auditor.py`)
- **Mode F Engage** — engagement-style follow-ups (`services/mode_f_engage.py`)

---

## MCP Integration (Claude Desktop / CLIs)

**Endpoint**: `https://auremcto.com/api/aurem-dev/mcp`
**Protocol**: JSON-RPC 2.0 over Streamable HTTP, spec `2025-03-26`
**Auth**: `Authorization: Bearer <token>` — either a JWT or an `sk-aurem-…` API key minted at `/settings → MCP keys`.

### Tools

| # | Name | Description |
| :---: | :--- | :--- |
| 1 | `list_projects` | User's connected projects |
| 2 | `ship_code` | Submit a coding task (Mode C enqueue) |
| 3 | `get_task_status` | Poll a task by id |
| 4 | `get_recent_commits` | Last N commits for a project's repo |

`ship_code` carries `readOnlyHint=false` + `destructiveHint=false`; the read tools carry `readOnlyHint=true` so MCP clients can suppress the destructive-action warning.

### OAuth 2.1 + PKCE

For hosted IDEs / multi-tenant clients:

```
GET  /oauth/authorize?client_id=…&redirect_uri=…&code_challenge=…&code_challenge_method=S256&scope=mcp
POST /api/aurem-dev/oauth/token       (PKCE code exchange → sk-aurem-… key)
GET  /.well-known/oauth-authorization-server
GET  /.well-known/mcp
```

Discovery is wired both at `/api/aurem-dev/oauth/.well-known/...` and the root `/.well-known/...` for strict MCP clients.

---

## Pricing (USD, flat)

| Plan | Price | Tasks / mo | Modes | Notes |
| :--- | :---: | :---: | :--- | :--- |
| **Free** | $0 | 10 | Swift only | Direct commits to your repo |
| **Starter** | **$9 / mo** | 50 | ⚡ Swift | Live worker tape, email support |
| **Pro** | **$19 / mo** | 300 | Swift + Pro | Project Brain, parallel agents, VS Code ext. |
| **Team** | **$49 / mo** | 400 | Swift + Pro + Maxx | Admin dashboard, priority queue, shared brain |

Annual plans save **20 %** ($86 / $182 / $470). No token meter. No model gates. No surprises.

Founder accounts (`FOUNDER_EMAILS`) bypass token enforcement entirely.

---

## Security (Vanguard 007)

`services/vanguard_scanner.py` ships the Vanguard 007 catalog inline — pure stdlib regex, no LLM cost. **Critical** patterns block commits; **warning** patterns surface in the PostTaskScan widget.

Critical (commit-blocker) patterns:

- `aws_access_key`, `aws_secret_key`
- `generic_api_key`, `generic_secret`
- `github_token` (`ghp_` / `gho_` / `ghu_` / `ghs_` / `ghr_`)
- `slack_token` (`xox*`)
- `private_key` (RSA / DSA / EC / OPENSSH / PGP)
- `password_assignment`, `token_assignment`

Plus runtime audits via:
- **`vanguard_verify_agent`** — second-pass LLM diff review
- **`post_task_scanner`** — runs after every commit
- **`vanguard_audit`** — exposed at admin `/admin/vanguard`

---

## Environment Variables

Required at minimum (see `backend/.env.example`):

```bash
# Storage
MONGO_URL=mongodb://...
DB_NAME=aurem_dev

# Auth + secrets
JWT_SECRET=<long random>
AUREM_MASTER_KEY=<fernet key>
AUREM_CTO_MASTER_KEY=<fernet key>   # iter 212m-9: encrypts BYOH SSH private keys
ADMIN_EMAIL=you@example.com
FOUNDER_EMAILS=founder@example.com,cofounder@example.com

# LLM gateway (REQUIRED — no fallback to direct providers)
OPENROUTER_API_KEY=sk-or-...
EMERGENT_LLM_KEY=sk-emergent-...   # used by Claude / GPT image / Nano Banana

# Stripe (live or test — both accepted)
STRIPE_API_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_STARTER_PRICE_ID=price_...
STRIPE_PRO_PRICE_ID=price_...
STRIPE_TEAM_PRICE_ID=price_...
STRIPE_STARTER_ANNUAL_PRICE_ID=price_...
STRIPE_PRO_ANNUAL_PRICE_ID=price_...
STRIPE_TEAM_ANNUAL_PRICE_ID=price_...

# GitHub
GITHUB_OAUTH_CLIENT_ID=Iv1...
GITHUB_OAUTH_CLIENT_SECRET=...
GITHUB_REDIRECT_URI=https://auremcto.com/api/aurem-dev/github-oauth/callback
GITHUB_ORG=your-org              # optional repo-scope restriction

# ORA upstream
ORA_API_KEY=...
ORA_BASE_URL=https://aurem.live

# Frontend hosting
APP_URL=https://auremcto.com
CORS_ORIGINS=https://auremcto.com,https://www.auremcto.com

# Email digest (optional)
RESEND_API_KEY=re_...
RESEND_FROM_EMAIL=ora@auremcto.com
DIGEST_FROM=ora@auremcto.com

# Observability (optional)
SENTRY_DSN=
SENTRY_ENV=production
SENTRY_RELEASE=iter187

# Search / scraping (optional)
FIRECRAWL_API_KEY=
TAVILY_API_KEY=

# Deploy bridges (optional)
VERCEL_API_TOKEN=
```

Frontend (`frontend/.env`) needs **only**:

```bash
REACT_APP_BACKEND_URL=https://<your-pod>.preview.emergentagent.com
WDS_SOCKET_PORT=443
```

---

## Running locally

```bash
# Backend
cd backend
pip install -r requirements.txt
# .env must be filled before this point
uvicorn main:app --reload --port 8001

# Frontend (separate shell)
cd frontend
yarn install
yarn dev          # vite dev server, port 3000
```

MongoDB needs to be reachable at `MONGO_URL`; the app provisions all indexes on boot.

---

## Project layout

```
backend/
├── main.py                  # FastAPI app + router wiring
├── routers/                 # 30 routers, 188 endpoints
│   ├── chat.py              # 12 endpoints incl. ORA panel + support-email draft
│   ├── cto_projects.py      # 20 endpoints — projects, tasks, SSE stream
│   ├── mcp.py               # JSON-RPC MCP server
│   ├── oauth.py             # OAuth 2.1 + PKCE
│   ├── payments.py          # Stripe checkout + webhook
│   ├── admin.py             # 57 admin endpoints
│   └── …
├── services/                # 60+ service modules
│   ├── agents.py            # 5 specialised agents
│   ├── smart_router.py      # model selection (iter 165)
│   ├── orchestrator.py      # main chat orchestration
│   ├── vanguard_scanner.py  # secret + dangerous-code regex catalog
│   ├── project_brain.py     # per-project memory
│   ├── graph_builder.py     # code symbol graph
│   └── …
├── cto_services/            # shared db + auth helpers
├── scripts/                 # init_prod_collections, seed_admins
└── tests/                   # pytest regression suite

frontend/
├── src/
│   ├── pages/               # 26 pages — Landing, Dashboard, Pricing, Admin*…
│   ├── components/          # 37 components — ChatPanel, ORASidePanel, PricingCards…
│   ├── hooks/               # useORAPanel, useWarmStart, useTextToVoice, useChatStream
│   └── lib/api.js           # axios instance + SSE chat stream helper
└── vite.config.js

vscode-extension/            # v0.2.0 — same backend, status bar control
docs/                        # banner.svg + assets
memory/                      # PRD.md, RECURRING_ISSUES, test_credentials
```

---

## Differentiators

- **Flat pricing.** $19/mo Pro covers 300 tasks. GitHub Copilot's June 2026 move to usage-based billing is reportedly costing heavy users $750+/mo. We don't meter tokens.
- **No PR workflow.** Code lands as a real commit on the branch you specified. Rollback is `git revert`.
- **Read-aware.** Repo context + Project Brain + Knowledge Graph feed the planner *before* it writes — fewer "where do I put this" turns.
- **Security is on by default.** Vanguard 007 regex pass + Claude verify agent + post-task scanner run on every commit. No opt-in.
- **MCP-native.** Claude Desktop / Claude Code / Cursor can drive ORA without leaving the editor — the same backend the web chat uses.
- **OpenRouter-only LLM path.** No direct OpenAI / Anthropic SDKs in production — one provider, one bill, one place to monitor.

---

## License

Proprietary © 2026 Aurem CTO. All rights reserved.
