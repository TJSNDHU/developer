# AUREM Dev — PRD

## Original Problem Statement
User uploaded `aurem-dev.zip` + `EMERGENT_PROMPT.md`. Goal: make the **AUREM Dev** developer platform fully working, production-ready, and runnable.

Stack expected per prompt:
- Backend: FastAPI on :8001 with `/api/aurem-dev/*` route prefix
- Frontend: React + Vite on :3001 (adapted to **:3000** for the Emergent supervisor environment)
- DB: MongoDB (used local mongo on 27017 instead of the Atlas URL from the prompt since the pod can't reach external clusters)
- LLM chat: Groq → OpenRouter → Emergent fallback (only Emergent universal key configured)

## Architecture (delivered)
```
/app
├── backend/                            FastAPI app
│   ├── main.py                         load_dotenv → wire 13 routers under /api/aurem-dev
│   ├── server.py                       supervisor shim (from main import app)
│   ├── routers/
│   │   ├── auth.py                     signup / login / me  ← NEW
│   │   ├── chat.py                     send / history       ← NEW (Emergent LLM)
│   │   └── (deploy, vault, stacks, domain, github_bot,
│   │        harden, trust, chat_commits, engagement,
│   │        unlock, projects)          relative imports patched to cto_services.*
│   ├── cto_services/                   auth (JWT HS256), db (handle), crypto, stacks, codebase_indexer
│   ├── services/                       llm (lazy env), orchestrator, project_generator,
│   │                                   github_auto, mongo_provisioner, tools_bridge
│   ├── shared/                         memory_tiers, security/hmac, providers, etc. (unchanged)
│   └── templates/stacks/               react-fastapi, nextjs-node, vue-express, plain-html
├── frontend/                           React 18 + Vite 5 + Tailwind
│   ├── vite.config.js                  port 3000, /api proxy → backend, REACT_APP_BACKEND_URL define
│   ├── src/
│   │   ├── App.jsx                     10 routes (Landing, Login, Signup, Dashboard, Deploy, …)
│   │   ├── lib/api.js                  axios instance + Bearer interceptor + getUser/setUser
│   │   ├── components/
│   │   │   ├── Shell.jsx               sidebar nav + api-online pill + user card + logout
│   │   │   └── ChatPanel.jsx           message thread + /chat/send caller
│   │   ├── pages/Landing.jsx           hero — "Build with an autonomous CTO"
│   │   ├── pages/Login.jsx             POST /auth/login
│   │   ├── pages/Signup.jsx            POST /auth/signup
│   │   ├── pages/Dashboard.jsx         chat panel (requires auth)
│   │   ├── pages/Deploy.jsx            GET/POST /deploy/config + /deploy/run + history
│   │   ├── pages/Database.jsx          POST /projects/create (provisions Mongo DB)
│   │   ├── pages/Domain.jsx            GET/POST /domain/config + verify DNS
│   │   ├── pages/Tokens.jsx            GET /auth/me + /streak/me
│   │   ├── pages/Analytics.jsx         GET /trust/uptime + /trust/deploy-count
│   │   └── pages/Settings.jsx          profile + GitHub status + vault audit-log
│   └── index.html                      title "AUREM Dev — Sovereign CTO"
├── infra/                              docker-compose + outbox worker (kept for self-host)
└── memory/                             PRD.md + test_credentials.md
```

## Key Implementation Notes
- **load_dotenv ordering**: Moved BEFORE router imports — fixes services/llm.py reading `os.getenv` at import time.
- **Relative imports**: Routers' `from ..services.auth/db/crypto/stacks` → `from cto_services.auth/db/…` (the original aurem-dev zip referenced a host-app layout that doesn't exist here).
- **Chat fallback chain**: `_groq_key()`, `_openrouter_key()`, `_emergent_key()` lazy-read so providers can be enabled later without restart.
- **`tools_bridge` upstream**: Calls `https://aurem.live/api/ora-tools/list` (returns 401 — no upstream JWT). Falls through cleanly with empty tool catalog.
- **JWT**: HS256, 30-day expiry, secret in `.env`.
- **Frontend env**: Vite reads `REACT_APP_BACKEND_URL` and exposes it via `define` so legacy `process.env.REACT_APP_BACKEND_URL` works.

## Implemented (2026-01-29)
- ✅ Backend boots, MongoDB connects (local), 13 routers wired
- ✅ JWT auth signup / login / me end-to-end
- ✅ Chat via Emergent universal key (Claude sonnet 4.5)
- ✅ Frontend Landing / Login / Signup / Dashboard (chat) / Deploy / Database / Domain / Tokens / Analytics / Settings
- ✅ Dark sodium-amber aesthetic (Cinzel serif, JetBrains Mono accents, Jost body)
- ✅ Health-pill sidebar status + logout
- ✅ E2E: 10/10 pytest backend tests pass; full Playwright UI flow passes
- ✅ Test user seeded (`test@aurem.dev` / `testpass123`)

## Backlog (P1)
- Wire `GROQ_API_KEY` + `OPENROUTER_API_KEY` for redundant LLM ladder
- GitHub OAuth — currently `GITHUB_TOKEN` is blank; `projects/create` GitHub push will fail gracefully
- Cloudflare tunnel + outbox worker (`infra/outbox/`) — Docker-compose pieces kept for self-hosted runs
- Streaming chat endpoint (currently non-streaming `/send`); SSE / WebSocket variant for token-by-token UI
- Session memory persistence in MongoDB (orchestrator supports it; chat router not yet passing `mongo_client`)
- Real DNS verification implementation (`/domain/verification/{domain}` currently uses stub)

## Backlog (P2)
- ProjectWorkspace (file tree + AI code edits) UI
- Save-to-GitHub one-click flow
- Token wallet refill cron + Stripe billing
- Audit log viewer with filtering

## Test Status
- Backend pytest: 10/10 ✅ (`/app/backend/tests/test_aurem_backend.py`)
- Frontend Playwright: full flow ✅ (landing → signup → dashboard chat → all nav → logout)
- Report: `/app/test_reports/iteration_1.json`

## Credentials
See `/app/memory/test_credentials.md`
