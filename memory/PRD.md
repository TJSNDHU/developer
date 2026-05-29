# AUREM Dev — PRD

## Original Problem Statement
User uploaded `aurem-dev.zip` + `EMERGENT_PROMPT.md`. Goal: make the **AUREM Dev** developer platform fully working, production-ready, and runnable.

Stack:
- Backend: FastAPI on :8001 with `/api/aurem-dev/*` route prefix
- Frontend: React + Vite on :3000 (adapted for the Emergent supervisor)
- DB: local MongoDB on 27017
- LLM chat: Groq → OpenRouter → Emergent fallback (Emergent universal key wired; others left blank)

## Implemented Iterations

### Iter 1 — MVP (2026-01-29)
- Adapted aurem-dev zip to supervisor: ports 3000/8001, `server.py` shim for uvicorn
- New `routers/auth.py` (signup / login / me — JWT HS256, 30-day expiry)
- New `routers/chat.py` (send / history)
- Fixed `cto_services/auth.py` + `db.py` (proper JWT verifier, `get_db()`)
- Patched all routers' broken relative imports `..services.*` → `cto_services.*`
- Moved `load_dotenv()` before router imports, lazy-read env in `services/llm.py`
- Frontend rewritten: clean Cinzel + Jost + JetBrains-Mono dark amber aesthetic
- Pages: Landing, Login, Signup, Dashboard, Deploy, Database, Domain, Tokens, Analytics, Settings
- Result: 10/10 backend pytest pass, full Playwright E2E pass

### Iter 2 — Chat sessions + SSE streaming (2026-01-29)
- `routers/chat.py` expanded to 5 endpoints:
  - `POST /send` — now persists turns to `db.chat_sessions`
  - `POST /stream` — SSE token streaming via FastAPI `StreamingResponse` with `text/event-stream`. Emits `data: {meta}` → `data: {token}` → `data: {done}`. Persists on completion.
  - `GET /history?session_id=X` — last 20 turns for current user
  - `GET /sessions` — list of sessions sorted by `updated_at` desc
  - `DELETE /sessions/{id}` — remove session
- Frontend `lib/api.js`: added `newSessionId()` (uses crypto.randomUUID()) and `streamChat()` (fetch + ReadableStream SSE parser)
- Frontend `Shell.jsx`: `SessionCtx` provider, "Recent Chats" sidebar section, new-chat (+) button, delete-session trash icon, active session highlighted, localStorage key `aurem_active_session` for refresh persistence
- Frontend `ChatPanel.jsx`: loads `/chat/history` on session change, streams via `streamChat()` with token-by-token cursor, Stop button via AbortController, "thinking…" indicator during pre-token latency
- Cross-user isolation verified (User B can't read/delete User A's sessions)
- Result: 10/10 new pytest + 10/10 regression + full Playwright E2E pass

## Backlog (P1)
- Wire `GROQ_API_KEY` + `OPENROUTER_API_KEY` for 3-provider LLM ladder
- True per-token streaming from provider (currently full response → chunked SSE)
- GitHub OAuth — `GITHUB_TOKEN` blank; `projects/create` GitHub push fails gracefully
- Real DNS verification implementation (currently stub)

## Backlog (P2)
- ProjectWorkspace (file tree + AI code edits) UI
- Save-to-GitHub one-click flow
- Token wallet refill cron + Stripe billing
- Audit log viewer with filtering
- Cloudflare tunnel + outbox worker (`infra/outbox/`) — kept for self-host

## Test Files
- `/app/backend/tests/test_aurem_backend.py` — iter1 (health, auth, /chat/send, stacks)
- `/app/backend/tests/test_aurem_chat_persistence.py` — iter2 (history, sessions, delete, SSE, isolation)
- Reports: `/app/test_reports/iteration_1.json`, `/app/test_reports/iteration_2.json`

## Credentials
See `/app/memory/test_credentials.md`.
