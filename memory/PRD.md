# AUREM Dev / Aurem CTO — PRD

## Original Problem Statement
User uploaded `aurem-dev.zip` to build a developer platform. Evolved into **Aurem CTO**: a multi-project workspace where developers connect client GitHub repos (OAuth or PAT), chat with an AI scoped per project, queue background tasks to clone repos, apply AI fixes, and push back to GitHub. Premium glassmorphic UI overhaul is the next major phase.

Stack:
- Backend: FastAPI on :8001 with `/api/aurem-dev/*` route prefix
- Frontend: React + Vite on :3000
- DB: local MongoDB
- LLM: DeepSeek V3 via OpenRouter for chat; Emergent LLM key for Maxx-mode watchdog

Production deploy: `auremcto.com`. Preview/dev: `launch-pad-237.preview.emergentagent.com`.

## Implemented Iterations

### Iter 1–4 (Jan 2026)
- MVP: auth, chat, session persistence, SSE streaming, session titles
- Single-provider DeepSeek V3 via OpenRouter (privacy-locked: `data_collection: deny`)
- Token billing system + TokenBell UI
- Inline live HTML/JSX preview via Babel-standalone in iframe

### Iter 5 — Aurem CTO Multi-Project (Jan 2026)
- New `routers/cto_projects.py` — add/list/delete client GitHub projects, submit AI tasks, background worker (clone → AI fix → push)
- New `routers/github_oauth.py` — GitHub OAuth flow
- New `components/TabBar.jsx` — Emergent-style tab bar per project on dashboard
- `pages/Projects.jsx` — CRUD for client projects
- Per-project chat scoping (session keyed to `project_id` in localStorage + DB)

### Iter 6 — P0 Bug Sweep (May 2026)
Fixed all 5 user-reported bugs from message 414:
- **BUG 1 — PAT not reading**: Project's `github_token` now properly stored and used in clone/push URL (preferred over user OAuth).
- **BUG 2 — Edit save not working**: Added `PATCH /cto/projects/{id}` endpoint + `EditDialog` in Projects.jsx. Also fixed local state sync after save (parent `refresh()` now keeps `active` project in sync).
- **BUG 3 — Chat input cursor refocus**: `setTimeout(() => taRef.current?.focus(), 80)` on stream `done`.
- **BUG 4 — Copy/Like/Dislike vanished**: `ActionBtn` row in `MessageBubble` (assistant non-streaming, non-system, non-error). New `POST /chat/feedback` endpoint persists vote into `turns[idx].feedback`.
- **BUG 5 — Chat history vanishing** (CRITICAL): Root cause was `_persist_turn` had a MongoDB WriteError 40 — `project_id` was being set in both `$setOnInsert` and `$set` simultaneously, causing every persist to fail silently. Fixed by moving `project_id` to `$setOnInsert` only, also added `project_id` to function signature and added new `/chat/sessions?project_id=X` filter to scope sidebar listing.
- Verification: 12/12 new pytest + 20 prior tests pass on regression. Full Playwright E2E pass on 5 bug flows.

### Iter 7 — Project-Aware Chat (May 2026)
Bug: User on a project tab asked "scan my repo" and got "I don't have access" — the chat had project NAME injected but no real file context.

Fixed by new `services/repo_context.py`:
- Fetches GitHub recursive tree via `GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1`
- Inlines up to 10 priority files (README, package.json, requirements.txt, entry points, configs) capped at 15KB total
- Injects as system prompt in `chat_with_tools` for both `/chat/send` and `/chat/stream`
- 30-minute Mongo cache (`db.repo_contexts`) keyed by `project_id`, invalidated on PATCH (PAT/branch change)
- Graceful 401/404 messaging when PAT bad or branch missing

Verified end-to-end: asking "what's in my repo?" on a connected project now returns real file listings; "what does this project do?" returns content-aware answers based on the README.

### Iter 8 — URL Fetching in Chat (May 2026)
Bug: User asked AI to read a shared link → AI said "I can't access the internet". DeepSeek has no native browsing.

Fixed by new `services/url_fetcher.py`:
- Regex-extracts up to 5 URLs from the user's prompt
- Parallel-fetches each (10s timeout, 6KB cap per URL, 20KB combined budget)
- BeautifulSoup-strips HTML to readable text, prefers `<main>`/`<article>` over chrome
- Passes through JSON / markdown / plain-text responses as-is
- Captures page title separately
- **SSRF guard**: blocks loopback / private / link-local / reserved IPs (`localhost`, `127.0.0.1`, `10.x`, etc.) so the bot can't be tricked into scanning internal infra
- Failures (timeout/404/blocked) degrade gracefully — one bad URL doesn't break the others
- Result is injected as system context alongside `repo_context` in `/chat/send` and `/chat/stream`

Verified: passing `https://fastapi.tiangolo.com` to chat → AI returns accurate content-aware summary. 404 URL → reports cleanly. `http://localhost:8001` → blocked.

`beautifulsoup4` added to `requirements.txt`.

## Active Phase / Next Up

### P1 — Premium UI/UX Redesign (NOT STARTED)
User provided detailed prompt (Message 412 WhatsApp screenshot) requesting:
- Glassmorphic textures, edge-to-edge layout
- Floating top nav
- Auto day/night mode
- Premium minimalist feel

Plan: call `design_agent_full_stack` to produce design guidelines, then modularize the now-very-large `ChatPanel.jsx` while applying the refactor.

## Backlog (P2)
- Stripe integration for paid tier / token recharge
- Per-project deploy buttons (Vercel/Netlify)
- Encrypt `github_token` at rest (Fernet) in `cto_projects` collection
- ChatPanel.jsx modularization (currently ~800 LOC, handles too many concerns)
- Fix transient "api offline" flash on first mount

## Data Models (MongoDB)
- `dev_users`: `{user_id, email, tokens_remaining, github: {access_token, login}}`
- `chat_sessions`: `{session_id, user_id, project_id, title, last_message, updated_at, turns: [{role, content, ts, provider, watchdog?, feedback?}]}`
- `cto_projects`: `{project_id, user_id, name, github_url, github_owner, github_repo, github_token, branch, tech_stack, status, tasks_done, created_at}`
- `cto_tasks`: `{task_id, project_id, user_id, task, status, steps[], commit_sha, result, error, created_at}`

## Key API Endpoints
- `POST /api/aurem-dev/chat/send|stream` — accepts `project_id` for scoping
- `GET /api/aurem-dev/chat/history?session_id=X` — returns turns incl. feedback
- `GET /api/aurem-dev/chat/sessions?project_id=home|p_xxx` — filtered sidebar list
- `POST /api/aurem-dev/chat/feedback` — `{session_id, turn_index, vote: 'up'|'down'}`
- `POST /api/aurem-dev/cto/projects/add` — `{name, github_url, github_token, branch, tech_stack}`
- `GET /api/aurem-dev/cto/projects/list` — excludes `github_token` from response (security)
- `PATCH /api/aurem-dev/cto/projects/{id}` — `{github_token?, branch?, tech_stack?}`
- `POST /api/aurem-dev/cto/tasks/submit` — queues background task

## Credentials
See `/app/memory/test_credentials.md`.

## Test Coverage
- `/app/backend/tests/test_aurem_backend.py` — iter1 (health, auth, /chat/send, stacks)
- `/app/backend/tests/test_aurem_chat_persistence.py` — iter2 (history, sessions, delete, SSE, isolation)
- `/app/backend/tests/test_aurem_p0_bugs.py` — iter6 (PAT, edit PATCH, feedback API, persistence with project_id, project filter, etc.)
- `/app/backend/tests/test_llm_provider.py` — iter4 (privacy assertions, deepseek-only)
- Reports: `/app/test_reports/iteration_{1,2,3}.json`
