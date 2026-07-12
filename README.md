# ORA by Aurem CTO
### The AI Engineer That Actually Commits.

> No IDE. No token billing. No broken loops. Just code that ships.

[![Founders 500](https://img.shields.io/badge/Founders-500-orange)]()
[![12k+ Commits](https://img.shields.io/badge/Commits-12k%2B-orange)]()
[![4.9★ Rating](https://img.shields.io/badge/Rating-4.9★-orange)]()
[![$9/month](https://img.shields.io/badge/Price-%249%2Fmonth-orange)]()

Live: **[auremcto.com](https://auremcto.com)** · Sister app: **[aurem.live](https://aurem.live)**

---

## Table of Contents

- [What ORA is](#what-ora-is)
- [Architecture Map](#architecture-map)
- [Feature Log — What Actually Ships Today](#feature-log)
- [Pricing / Tiers](#pricing--tiers)
- [Head-to-head](#head-to-head)
- [Tech Stack](#tech-stack)
- [Runbook (dev preview)](#runbook)
- [Support & Status](#support--status)

---

## What ORA is <a id="what-ora-is"></a>

ORA is an autonomous AI software engineer. It connects to your GitHub
repo, reads your codebase, writes production-ready code, runs security
scans, and commits directly to your branches — all from a browser tab,
no IDE required.

Two products in the same house:
- **auremcto.com** — this repo. The AI CTO / codebase engineer.
- **aurem.live** — sister app. Autonomous AI workforce for lead-gen,
  outreach, and business automation (Scout → Closer → Followup agents
  running 24/7).

---

## Architecture Map <a id="architecture-map"></a>

Deeper per-layer files live in `memory/architecture/01…06_*.md`. This
is the one-screen mental model.

```
┌─────────────────────────────────────────────────────────────┐
│                     USER (Browser / PWA)                     │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS
┌──────────────────────────▼──────────────────────────────────┐
│              FRONTEND — React SPA (port 3000)                │
│  Tailwind + Shadcn UI + Context API + SSE streaming          │
│  38 pages · ~91 custom components  (see memory/architecture/02) │
└──────────────────────────┬──────────────────────────────────┘
                           │  /api/aurem-dev/*  (Kubernetes ingress)
┌──────────────────────────▼──────────────────────────────────┐
│              BACKEND — FastAPI (port 8001)                   │
│  49 include_router calls in `backend/main.py`                │
│  Router → Core (Parliament) → 92 Services → External APIs    │
└──────────┬───────────────┬───────────────┬──────────────────┘
           │               │               │
     ┌─────▼─────┐   ┌─────▼──────┐  ┌─────▼──────────────┐
     │  MongoDB  │   │ GitHub API │  │ LLM Providers        │
     │ (Motor)   │   │ PAT + OAuth│  │ OpenRouter · Groq ·  │
     │  ~76 cols │   │            │  │ Anthropic (Emergent) │
     └───────────┘   └────────────┘  │ Council A primary =  │
                                     │ claude-sonnet-4.5    │
                                     └──────────────────────┘
```

### Layer responsibilities (one line each)

| Layer | Job | Docs |
|-------|-----|------|
| Frontend | React SPA — chat UI, dashboards, wizards, admin suite. `REACT_APP_BACKEND_URL` only. 38 pages · ~91 custom components. | `memory/architecture/02_frontend_spec.md` |
| Backend Core "Parliament" | Intent classification + multi-agent routing (`backend/core/`) | `memory/architecture/03_backend_core_and_routers.md` |
| Backend Routers | 49 HTTP endpoint groups (`backend/routers/`) — auth, scanning, fixing, repo, business, deploy, admin, QA, suggestions | `03_…` |
| Backend Services | 92 modules (`backend/services/`) — AI orchestration, scanners, fix engine, repo intelligence, safety guards, learning, billing | `memory/architecture/04_backend_services.md` |
| Data Layer | MongoDB via Motor — users, projects, tasks, fixed-findings ledger, audit logs | `memory/architecture/05_data_and_integrations.md` |
| Business Logic | Tier/quota system (`subscription_tiers.py` + `scan_fix_quota.py`) | `memory/architecture/06_business_logic_and_open_items.md` |

### The 5 core patterns (must-follow)

1. **Parliament Pattern** — every AI request is classified by
   `core/intent_gateway.py` before `core/parliament.py` picks the
   agent. No endpoint may call an agent directly.
2. **Fixed-Findings Ledger** — an applied fix goes into
   `cto_fixed_findings` and stays hidden from rescans **until its PR
   merges**. Prevents the health score from falsely regressing.
3. **Quota-as-Tasks** — 1 fix = 1 task. No severity-based pricing.
   Gating in `services/scan_fix_quota.py`. Deduction only on success
   via `record_scan_fixes()` — a failed fix never burns a task.
4. **Local Snapshot Cache** — `.aurem_cache` holds a local git-tree
   snapshot so scans stay fast and GitHub secondary rate limits stay
   quiet. New repo-reading features read the snapshot first.
5. **Guard Layers** — `services/hallucination_guard.py` +
   `services/citation_guard.py` both verify LLM output before any
   fix hits a user repo.

### Council A live status

Council A primary is `anthropic/claude-sonnet-4.5` (via OpenRouter).
The on-boot probe + a 15-min background re-probe write outcomes to
`council_health_probes` and expose live state at
`GET /api/aurem-dev/admin/council/health`. The admin overview shows
a prominent orange banner within one probe cycle if the primary ever
degrades — the fallback is `z-ai/glm-5.2`, whose malformed XML
tool-call emissions are absorbed by a lenient extractor in
`services/tools_bridge.py`.

---

## Feature Log — What Actually Ships Today <a id="feature-log"></a>

### 🛡️ Vanguard Security Scanner
- 25-pattern pre-commit security scan on every commit
- Detects: secrets, SQL/NoSQL injection, JWT replay, XSS, CSRF, path
  traversal, IDOR, missing auth, SSTI, ReDoS, LPDoS
- Critical findings block the commit automatically
- Security badge always visible in composer toolbar with red-dot
  critical count
- No other AI coding tool ships this. Reference: Lovable
  CVE-2025-48757 (April 2026).

### 🔄 Loop Mode — Verified Execution
- PLAN → EXECUTE → VERIFY → SECURITY SCAN → SHIP
- Plan shown before any code is written; user approves
- Files written one at a time with live progress
- `ruff` + `eslint` run after every file; auto-retry 3× on failure
- Self-heal: ORA reads errors and rewrites automatically
- Only commits after every step passes; 90 s watchdog +
  `StreamHealthPill` feedback prevents stuck runs

### 🏥 Codebase Health Scanner — 7 categories
- Security · Performance · Code Quality · Dependencies · Database ·
  HTTP Headers · Docker CIS
- Individual fix buttons per finding (micro-satisfaction loop)
- Dramatic 0–100 health score with urgency copy
- Full-scan aggregation via `services/loop_full_scan.py` with depth
  gate + 3× auto-retry

### 💬 Chat-native Scan Commands *(Iter 212m-190)*
Type `/` in the composer:
- `/scan` — all 4 scanners
- `/health-scan` · `/security-scan` · `/bug-hunt` · `/docker-scan`

Above the composer, `ScanStatusStrip` shows scan lifecycle events —
in-progress spinner, just-completed critical/high totals, X-dismiss.
Grafted straight into the real production `ChatPanel.jsx` (not the
v2 preview page, which stays a hardcoded visual mock per its own
docstring).

### 📮 Founder Suggestion Box *(Iter 212m-193)*
- `POST /suggestions` — JWT-auth, body `{text}` only. User identity
  and active project come from the server, not the client.
- **Date-based** rate limit (1 per user per UTC day) — session
  bypasses do nothing.
- Background Groq pre-analysis writes `{summary, benefits[3],
  risks[3], effort, overlaps_existing, recommendation}`. Strict JSON
  validation; malformed responses set `analysis_failed: true` with
  raw output preserved.
- Explicitly isolated from `orchestrator.py`'s Council chain —
  a future Ask Advisor outage can't take the suggestion box down.
- User surface: sidebar dropdown → "Suggest a feature" modal.
- Admin surface: new `/admin` "Suggestions" tab with expandable
  "AI analysis — not a decision" chip + tick/cross that records
  `decided_by` for audit.

### ⚡ 4-Hop LLM Fallback Chain
- OpenRouter primary → DeepSeek direct → OR free chain → Groq
- Provenance tags show which hop served each response
- Silent failover — user never notices

### 🧠 ORA Council — self-learning
Every interaction is captured across five modes (Chat / Advice /
Code / Debug / Audit). Fine-tune threshold: 1000 interactions.

### 🔐 Security posture
- JWT tokens include `jti` + `iat`
- NoSQL operator sanitizer ASGI middleware blocks `$where`/`$expr`
- Request body size limit — LPDoS shield
- Global `unhandledrejection` handler ships client errors to
  `frontend_errors`

### 🎨 UI polish
- Token-by-token streaming
- Skeleton loading (no "Loading 80%" fake bar)
- Syntax highlighting + copy button on all code blocks
- Day/Night theme toggle (localStorage)
- `StreamHealthPill` — reconnect status above the composer
- Inline loop progress: PLAN✓ → EXECUTE⏳ → VERIFY○ → SCAN○ → SHIP○

### 📊 SEO / GEO / AEO
- Meta Pixel on every page
- JSON-LD: SoftwareApplication · Organization · FAQPage · WebSite
- `llms.txt` for AI-crawler discovery
- `robots.txt` explicitly allows every major AI crawler
- OG image + preview cards for social sharing

### 🔌 Integrations
Claude Desktop · Claude Code · Cursor · VS Code · Ollama (offline) ·
LM Studio · GitHub · MCP 2.4

### 🩺 Ops surface *(Iter 212m-192)*
- `GET /api/aurem-dev/admin/council/health` — live Council A status
- Persistent probe history in `council_health_probes`
- Admin banner + "LLM PROVIDER STATUS" pane on `/admin` overview
- `/connection-status` now probes GitHub's **contents** endpoint so
  a green sidebar dot means real file-read permission, not just
  repo visibility

---

## Pricing / Tiers <a id="pricing--tiers"></a>

Source of truth: `backend/services/subscription_tiers.py`.

| Tier | Price / mo | Tasks / mo | Fix tools | Bulk fix | Modes |
|------|------------|------------|-----------|----------|-------|
| Free | $0 | 10 | none (scans only) | ✗ | swift |
| Starter | $9 | 50 | vanguard-scan | ✗ | swift |
| Pro | $19 | 300 | vanguard + health | ✗ | swift, pro |
| Team | $49 | 400 | all 4 (vanguard, health, security, bug-hunt) | ✓ | swift, pro, maxx |
| Founder | $0 (internal) | unlimited | all 4 | ✓ | all |

**Rule:** 1 fix = 1 task, regardless of severity. Scan-fix quota
rolls into the same monthly meter as chat tasks (`services/usage.py`).

---

## Head-to-head <a id="head-to-head"></a>

| Feature | ORA | Cursor | Copilot | Bolt | Devin |
|---------|-----|--------|---------|------|-------|
| Pre-commit security scan | ✅ | ❌ | ❌ | ❌ | ❌ |
| 4-hop LLM fallback | ✅ | ❌ | ❌ | ❌ | ❌ |
| Self-learning per user | ✅ | ❌ | ❌ | ❌ | ❌ |
| Verified loop mode | ✅ | ❌ | ❌ | ❌ | partial |
| No IDE required | ✅ | ❌ | ❌ | ✅ | ✅ |
| Full codebase health scanner | ✅ | ❌ | ❌ | ❌ | ❌ |
| Chat-native `/scan` commands | ✅ | ❌ | ❌ | ❌ | ❌ |
| Fixed-findings PR-merge ledger | ✅ | ❌ | ❌ | ❌ | ❌ |
| Starting price | $9 | $20 | $10 | $20 | $500 |

---

## Tech Stack <a id="tech-stack"></a>

- **Frontend:** React 18 + Vite + Tailwind + Shadcn UI + Lucide icons
- **Backend:** FastAPI + Uvicorn + Motor (async MongoDB) +
  APScheduler + httpx
- **Database:** MongoDB (Motor async driver)
- **AI:** OpenRouter proxy + Groq direct + Anthropic via
  Emergent LLM Key + emergentintegrations
- **Auth:** Custom JWT (with `jti` + `iat`) + Google OAuth
  (Emergent-managed) + GitHub OAuth (identity only, PAT for repo I/O)
- **Real-time:** SSE (Server-Sent Events) for chat streams and fix
  progress. No websockets, no long-polling.
- **Cache / snapshots:** `.aurem_cache/` git-tree snapshots on disk

---

## Runbook (dev preview) <a id="runbook"></a>

Local services are supervisor-managed. Every URL/port/credential
comes from `.env` files — nothing is hardcoded.

```bash
# Backend logs
sudo supervisorctl status
tail -f /var/log/supervisor/backend.err.log

# Restart after .env or dependency change (hot-reload handles code edits)
sudo supervisorctl restart backend
sudo supervisorctl restart frontend

# Health
curl "$REACT_APP_BACKEND_URL/api/aurem-dev/system/health"

# Council A live status (admin-only)
curl "$REACT_APP_BACKEND_URL/api/aurem-dev/admin/council/health" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### Two environments

- **PREVIEW** (this codebase) — editable, hot-reload, testing surface.
- **PRODUCTION** (`https://auremcto.com`) — the live deploy. Code
  changes only reach production after a manual redeploy.

Every fix documented above lands on preview first. Production catches
up on the next redeploy.

---

## Support & Status <a id="support--status"></a>

- **Email:** ora@auremcto.com
- **Integrations docs:**
  [auremcto.com/integrations](https://auremcto.com/integrations)
- **Health:** [auremcto.com/api/aurem-dev/system/health](https://auremcto.com/api/aurem-dev/system/health)
- **Founder Suggestion Box:** in-app, sidebar dropdown → *Suggest a
  feature*

---

*Built by Aurem CTO · [auremcto.com](https://auremcto.com) ·
sister app [aurem.live](https://aurem.live)*
