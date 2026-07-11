# 01 — SYSTEM OVERVIEW & CORE DESIGN PATTERNS
(Load this FIRST in every session. High-level map — read before touching any layer. Files 02–06 cover each layer in depth.)

## HIGH-LEVEL ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────┐
│                      USER (Browser / PWA)                    │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS
┌──────────────────────────▼──────────────────────────────────┐
│              FRONTEND — React SPA (Port 3000)                │
│   Tailwind + Shadcn UI + Context API + SSE Streaming         │
│   37 pages, ~90 components (see file 02)                     │
└──────────────────────────┬──────────────────────────────────┘
                           │ /api/* (Kubernetes Ingress)
┌──────────────────────────▼──────────────────────────────────┐
│              BACKEND — FastAPI (Port 8001)                   │
│   46 Routers → Core (Parliament) → 87 Services → External    │
│   Entry: backend/main.py (all include_router calls live here)│
└──────────┬───────────────┬───────────────┬──────────────────┘
           │               │               │
     ┌─────▼─────┐   ┌─────▼──────┐  ┌─────▼──────────────┐
     │  MongoDB  │   │ GitHub API │  │ LLM Providers       │
     │ (Motor)   │   │ (PAT/OAuth)│  │ OpenRouter/Groq/    │
     └───────────┘   └────────────┘  │ Anthropic (Emergent)│
                                     └─────────────────────┘
```

## LAYER RESPONSIBILITIES (one line each)
- **Frontend**: React SPA — chat UI, dashboards, wizards, admin suite. Talks to backend only via `/api/*` using `REACT_APP_BACKEND_URL`. → file 02
- **Backend Core ("Parliament")**: intent classification + multi-agent routing (`backend/core/`). → file 03
- **Backend Routers**: 46 HTTP endpoint groups (`backend/routers/`) — auth, scanning, fixing, repo, business, deploy, admin. → file 03
- **Backend Services**: 87 modules (`backend/services/`) — AI orchestration, scanners, fix engine, repo intelligence, safety guards, learning, billing. → file 04
- **Data Layer**: MongoDB via Motor — users, projects, tasks, fixed-findings ledger, audit logs. → file 05
- **External Integrations**: GitHub REST API, LLM providers, Google Auth, Meta Pixel, SSE. → file 05
- **Business Logic**: tier/quota system (`subscription_tiers.py` + `scan_fix_quota.py`). → file 06

## THE 5 CORE DESIGN PATTERNS
(Check which pattern your change touches BEFORE writing code. Follow the existing convention — never invent a parallel one.)

1. **Parliament Pattern** — every AI request is classified first by `core/intent_gateway.py`, then routed by `core/parliament.py` to a specialized agent (Council / Debugger / Auditor / Engage). No endpoint may call an agent directly, bypassing intent classification.

2. **Fixed-Findings Ledger** — an applied fix is recorded in `cto_fixed_findings` (via `services/fixed_findings.py`) and stays hidden from rescans until its PR actually merges. This prevents the health score from falsely regressing. Never mark a finding permanently resolved before merge confirmation of its `commit_sha`.

3. **Quota-as-Tasks** — 1 fix = 1 task, always. No severity-based pricing. Gating is by TOOL and FEATURE (bulk), enforced in `services/scan_fix_quota.py`. Deduction happens ONLY on successful fixes via `record_scan_fixes()` — a failed fix never burns a task. Scan-fix usage rolls into the same monthly task meter as chat tasks (`services/usage.py`).

4. **Local Snapshot Cache** — `.aurem_cache` holds a local git-tree snapshot so scans are fast and GitHub secondary rate limits aren't hit. Any new repo-reading feature reads from this cache first, never GitHub directly.

5. **Guard Layers** — `services/hallucination_guard.py` and `services/citation_guard.py` verify LLM output before any fix is applied. An LLM-generated fix that hasn't passed BOTH guards must never touch a user's repo.

## RULES FOR THE AI DEVELOPER (hard constraints)
1. Identify which of the 5 patterns your task touches and follow its convention. If a change would break a pattern, STOP and flag it explicitly before implementing.
2. All backend routes MUST be prefixed with `/api`. Frontend must use `process.env.REACT_APP_BACKEND_URL`; backend must use `MONGO_URL`/`DB_NAME` from `backend/.env`. No hardcoded URLs, ports, or credentials anywhere.
3. Never expose `github_token` (from `cto_projects`) in logs, API responses, or the frontend.
4. Never bypass `intent_gateway.py` → `parliament.py` for AI request routing.
5. Never apply an LLM fix without both `hallucination_guard` and `citation_guard` passing.
6. Two environments exist: PREVIEW (dev, editable) and PRODUCTION (https://auremcto.com, requires user redeploy). Code changes only affect preview until redeployed.
