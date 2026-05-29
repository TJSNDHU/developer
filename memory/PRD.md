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

### Iter 4 — OpenRouter → DeepSeek-V3 only (2026-01-29)
- `services/llm.py` rewritten as a single-provider gateway. Drops Groq + Emergent + OpenRouter-Claude. All traffic now goes to **`deepseek/deepseek-chat`** (DeepSeek V3) via OpenRouter.
- Privacy directives in every payload: `data_collection: "deny"`, `allow_fallbacks: false`, `order: ["deepseek","streamlake","deepinfra","novita"]`. OpenRouter enforces `data_collection: deny` across all routed providers — no host stores or trains on traffic.
- Headers: `X-No-Cache: true`, `HTTP-Referer: https://aurem.dev`, `X-Title: AUREM Dev`.
- If OpenRouter is unreachable, `call_llm_with_meta` returns `{ok:false, error:"LLM unavailable: ..."}` — **never** silently routes to Emergent / Groq / Anthropic.
- New file `/app/backend/tests/test_llm_provider.py` — **5/5 pass**: payload-privacy assertions, success path returns provider="deepseek", 5xx error path returns ok=False, network error path returns ok=False, missing API key raises.
- Side note: OpenRouter does not expose DeepSeek's first-party endpoint for this account — the privacy-compliant V3 hosts available are streamlake / deepinfra / novita. All three are bound by the same `data_collection: deny` directive so the privacy posture is preserved.

### Iter 3 — Session titles (2026-01-29)
- Added `_generate_title()` + `_maybe_set_title()` in `routers/chat.py`. After first user/assistant turn, fires a background `asyncio.create_task` that asks the LLM to summarize the prompt in 3-5 words Title Case (no punctuation), stores it as `session.title`.
- Idempotent: re-runs no-op if title already present or turns < 2.
- Updated `GET /chat/sessions` + `GET /chat/history` to return `title` field.
- Frontend `Shell.jsx` sidebar renders `title || last_message || "Untitled"` with bolder font weight when title is set.
- `ChatPanel.jsx` schedules a second `onTurnSaved()` ~2.8s after stream done so the sidebar picks up the freshly-generated title without a reload.
- Verified: "Help me design a Stripe checkout flow for my SaaS" → titled "Stripe Checkout Flow Design".
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
