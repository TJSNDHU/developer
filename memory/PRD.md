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

### Iter 9 — Clean Deployment Logs (May 2026)
Production deploy logs were noisy with repeated `services.tools_bridge ERROR list_tools failed: Client error '401 Unauthorized' for url 'https://aurem.live/api/ora-tools/list'`.

Cause: this deployment isn't paired with an `aurem.live` upstream account, so the optional tool catalog returns 401 on every chat call.

Fixed in `services/tools_bridge.py`:
- Downgraded expected 401/403/404 from ERROR → single INFO log
- Added process-lifetime circuit breaker (`_upstream_giving_up`) — first 401 trips it, subsequent calls short-circuit without any HTTP traffic
- New env var `DISABLE_UPSTREAM_TOOLS=1` to skip the call entirely from the start
- Tightened `list_tools` timeout from 60s → 10s (it's optional, no reason to wait)

Result: deployment logs are clean. Deployment agent confirmed the app is deployable (no actual blockers, just log noise).

### Iter 10 — MarkItDown File Upload (May 2026)
User requested: integrate Microsoft's [MarkItDown](https://github.com/microsoft/markitdown) so uploads (PDF/DOCX/XLSX/PPTX/images/CSV/etc.) auto-convert to Markdown before hitting the LLM — saves token cost and lets AI actually read binary files.

Installed `markitdown[all]==0.1.6` (pulls pdfminer, mammoth, openpyxl, python-pptx, magika, etc.).

New `routers/upload.py`:
- `POST /api/aurem-dev/upload/convert` — multipart `file`, JWT-gated
- 25MB request cap, 60K-char output cap with `truncated: true` flag
- Returns `{filename, content_type, original_size, md_size, markdown, truncated}`
- Drops upload to temp file with original suffix (MarkItDown uses suffix for format detection), converts, cleans up

Frontend `ChatPanel.jsx` `handleFiles` now has a smart fast path:
- ≤50 KB text-extension files → read in browser, no server roundtrip (unchanged from before)
- Everything else (PDF/DOCX/XLSX/images/large code/etc.) → multipart POST to `/upload/convert`, returned markdown gets appended to the chat input as `[File: name · 1.2 MB → 18 KB markdown]\n\n<md>`
- Max upload bumped from 50 KB → 25 MB to match backend cap
- Tooltip updated: "PDF, DOCX, XLSX, PPTX, images, code (max 25 MB)"

Verified end-to-end via curl: HTML → clean MD with headings/lists, CSV → markdown table, PDF (13KB) → text extracted, auth guard returns 401 without token.

### Iter 11 — Proactive Engineer Persona (May 2026)
User complaint: when given a task list, Aurem CTO was just summarizing it back ("This appears to be a comprehensive system update that addresses...") instead of producing an execution plan.

Root cause: The default system prompt was just `"You are ORA CTO Sovereign, running on the Legion laptop."` — passive and generic. With no behavioral anchoring, the model defaulted to summarizing what it saw.

Added `AUREM_CTO_PERSONA` constant in `services/orchestrator.py` that anchors EVERY chat turn with explicit rules:
1. **ANALYZE** — 1-sentence goal restatement
2. **PLAN** — numbered steps with concrete files/functions to touch
3. **RISKS** — call out breakage in 1-2 lines
4. **VERIFY** — state how to test
5. **ASK TO PROCEED** — end with "Ready to ship? Reply 'go' and I'll start with step 1."

Plus explicit prohibitions: no parroting user's own task list back, no "this appears to be...", no "Let me know if you have questions!" trailers, no claims that connected repo / fetched URLs are inaccessible.

Persona is always the floor of the system prompt; repo_context + url_context layer on top of it (not replace it).

Verified: prompting with the exact task list the user complained about now produces a proper 5-section execution plan ending with "Ready to ship? Reply 'go'…".

### Iter 12 — Live Project Preview Panel (May 2026)
User asked: clicking the Preview button should show the *actual* connected project's frontend (so code changes flow into the visible UI in real time), not just code blocks from chat.

New flow:
- `cto_projects` schema: added `preview_url` (optional public URL of the running site/dev server)
- `AddProject` and `UpdateProject` models accept it; `PATCH /cto/projects/{id}` honours it
- Add Project dialog: new "Live preview URL (optional)" field (`data-testid="proj-preview-url"`)
- Edit dialog: same field (`data-testid="proj-edit-preview-url"`)
- `ChatPanel.jsx`: when `activeProject.preview_url` is set, prepends a `{lang:"live_url", code:url, label:"Live Site"}` block at index 0 of PreviewPanel tabs; auto-opens panel on project switch (respects user's explicit close)
- `PreviewPanel.jsx`: new `live_url` block type renders `<iframe src={url}>` with full sandbox (allow-same-origin / forms / popups / modals) so the user's site works. Footer gets a new "Open" button (lucide `ExternalLink`) that opens the site in a new tab — useful when the site blocks iframe embedding via `X-Frame-Options`.

Empty state polish: when no preview URL is set, panel shows: *"No preview URL set for "<project>". Open Projects → Edit → 'Live preview URL' to add one."*

Verified backend end-to-end via curl (add → list → PATCH → list); UI screenshot confirms the Add dialog renders the new field. Frontend lint clean.

### Iter 13 — Commit Rollback Button (May 2026)
User requested: after a CTO task pushes a commit, show a Rollback button; always require two confirmations before reverting; wire and E2E test.

**Backend** (`routers/cto_projects.py`):
- New `POST /api/aurem-dev/cto/tasks/{task_id}/rollback` — body `{confirm: "ROLLBACK"}` (must echo string)
- Guards: 401 (no auth), 400 (wrong confirm, status!=done, no commit_sha, no PAT on project), 404 (unknown task / no parent project), 409 (already rolled back, rollback in progress, **previous rollback failed → manual intervention required**)
- Background worker `_run_rollback`: full-history clone, `git revert --no-edit -m 1 <sha>` (with fallback to plain revert for non-merge commits), `git push origin <branch>` — **never force-push, history preserved**
- Task doc gains: `rollback_status` (queued→running→done|failed), `rollback_sha`, `rollback_error`, `rollback_steps[]`, `rollback_started_at`, `rollback_completed_at`
- **Security fix**: PAT scrubbed (`_scrub()`) from every error/log string before persisting → no leak via Mongo

**Frontend** (`Projects.jsx`):
- `Undo2` icon import; `TaskRow` accepts `onRollback` callback
- Rollback button rendered ONLY when `status=='done' && commit_sha && !rollback_sha && !rbRunning && rollback_status !== 'failed'`
- `handleRollback` triggers TWO sequential `window.confirm()` dialogs — first explains revert semantics, second is final "are you sure?". Cancelling either aborts.
- Inline status line shows `rolling back…` / `reverted → <new_sha>` / `rollback failed`
- Expanded panel renders a `── rollback ──` section with all `rollback_steps[]` and any `rollback_error`
- Polling effect kept alive while `rollback_status` ∈ {queued, running} so UI updates live

**Test report**: `/app/test_reports/iteration_4.json`. Backend 13/13 + 22/22 regression pass. Testing agent flagged one HIGH UI bug (button still showing on failed rollbacks) + PAT-leak via stderr — **both fixed** in this iteration. New `/app/backend/tests/test_aurem_rollback.py` (13 tests) committed.

### Iter 14 — Hover-Only Copy Buttons (May 2026)
User: chat bubbles need a Copy button that shows ONLY on cursor hover and hides otherwise — both user messages (new) and assistant action row (was always-visible).

`ChatPanel.jsx` MessageBubble:
- Added `hover` state with `onMouseEnter`/`onMouseLeave` on the row
- **User bubbles**: new absolutely-positioned floating copy button (`data-testid="copy-user-{idx}"`), opacity 0 → 1 on hover, 0.15s transition
- **Assistant bubbles**: existing copy/👍/👎 action row now also opacity-toggled on hover (same transition)
- `pointer-events: none` when hidden so it doesn't intercept clicks

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
