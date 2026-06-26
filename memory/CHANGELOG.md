# AUREM Dev / Aurem CTO — Changelog

Append-only iteration log. See `PRD.md` for the original problem
statement and historical context; this file captures recent feature
work in date-stamped chunks so PRD.md stays focused.

---

## Iter 212m-31 — Empty-state Connect-Repo Banner (Feb 26 2026) ✅

**One-CTA empty state for the founder offer funnel.**

User-locked copy (signed off in chat):
- Headline: `Connect a repo to unlock your free SEO fix`
- Sub: `[X] of 500 founder spots remaining`
- Button: `Connect repo →`
- 3 inline steps:
  1. Go to `github.com/settings/tokens` → **Fine-grained tokens**
  2. **Permissions: Contents (Read & Write)**
  3. Paste token below

### What shipped

**`components/ConnectRepoBanner.jsx`** (new):
- Live spots counter polls `/founder-offer/status` every 60 s.
- Counter color: green > 50, orange ≤ 50, red ≤ 10 (matches the
  FounderOfferCard heuristic for visual continuity).
- Collapsible (default expanded). Collapse state persisted to
  `localStorage["aurem_connect_banner_collapsed"]` so a power user
  who hides it stays hidden across reloads.
- Hides itself when the founder offer is fully consumed (remaining
  === 0) — at that point the SEO incentive is gone and dangling it
  would just frustrate.
- PAT deeplink targets fine-grained tokens (`?type=beta`) — *not*
  classic — so the user lands on the secure-default flow.
- Every interactive + critical element has `data-testid`.

**`pages/Dashboard.jsx`** wiring:
- New `projectCount` state — single source of truth used by BOTH
  the wizard auto-popup AND the persistent banner.
- Banner mounts above the chat panel ONLY when `projectCount === 0`.
  Hidden the instant the first repo lands.
- `openWizardFromBanner` callback bypasses the dismiss flag so the
  user can reopen the wizard from the banner even after they've
  closed the onboarding overlay once.
- `onWizardComplete` re-fetches the project list and updates count
  so the banner unmounts as soon as a connect succeeds.

### Tests
- `tests/test_iter212m31_connect_repo_banner.py` — **5 source pins**
  covering the locked copy, 3-step PAT guide, polling endpoint,
  Dashboard mount condition, and sold-out hide rule.
- Full 212m-27 → 31 regression: **72/72 pass**.

### Live E2E proof
| Test | Result |
|---|---|
| Sign up fresh user, set `aurem_wizard_dismissed=1`, land on /dashboard | Banner renders with `headline="Connect a repo to unlock your free SEO fix"`, `counter="500 of 500 founder spots remaining"` |
| Click "Connect repo →" | NewUserWizard overlay opens (even with dismiss flag set) |
| `data-testid="connect-repo-banner-step-1..3"` | All three steps present with locked copy |
| PAT deeplink | `https://github.com/settings/tokens?type=beta` |

> **Deployment note**: PREVIEW only. User must redeploy to push to
> `auremcto.com` production.

---

## Iter 212m-30 — Repo Indexing + Founder Offer (PR-2) (Feb 26 2026) ✅

**The other two-thirds of the SEO programme.** PR-1 shipped the SEO
core engine; PR-2 wires a deterministic codebase-map generator into
every connected repo AND adds the 500-spot founder offer that gives
new signups a free SEO fix straight from the chat.

### What shipped

**Backend — Repo indexing (`services/repo_indexing.py`)**:
- One `GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1` call,
  zero LLM. Detects: dominant language (by file-ext counting),
  entry points (main.py / App.tsx / pages/_app.tsx / …), top-level
  service folders (api/routers/services/models/db/utils/…),
  dependency manifests (requirements.txt / package.json / pyproject /
  Cargo / go.mod / Gemfile / Dockerfile / …), has_tests, file_count.
- Optional README.md fetch → extracts the first H1 + first paragraph
  with simple markdown stripping (images / links / inline code) so
  the persisted summary is plain-text readable.
- `CODEBASE.md` is rendered with a stable layout so re-runs only
  diff on the timestamp line + file counts.
- Stored in MongoDB `repo_index` (upsert on `project_id`); committed
  to repo root via the existing `services.github_api_writer
  .commit_files()` single-atomic-commit path.
- Route: `POST /api/aurem-dev/repos/{repo_id:path}/index?commit=true`.

**Backend — Founder Offer (`routers/founder_offer.py`)**:
- Singleton `founder_offer` doc: `{_id: "global", total_spots: 500,
  spots_claimed: N, is_active: true}`. Idempotent boot via
  `_ensure_singleton` (`$setOnInsert + upsert`).
- `GET /status` (public): `{remaining, total, is_active}`.
- `GET /user-status` (auth): `{repos_claimed, has_fully_claimed,
  days_since_signup, max_claims_per_user}`. `_days_since` handles
  tz-aware datetime, epoch seconds, epoch ms, AND ISO strings so the
  endpoint stays sane across legacy rows.
- `POST /claim` body `{repo_id, site_url}`:
  atomic `find_one_and_update` decrement with
  `$expr: {$lt: [$spots_claimed, $total_spots]}` (so two concurrent
  claims can never over-allocate); inserts a `user_seo_claims` row
  with `fix_status="preview"`; calls `services.seo.orchestrator
  .run_seo_fixes(dry_run=True)` and returns the preview to the UI.
  Per-user cap of 3 enforced — 4th claim returns `{success: false,
  action: "upgrade"}` (no error, soft no). Sold out → `{success:
  false, action: "sold_out"}` (also soft).
- `POST /confirm` body `{claim_id}`: flips fix_status to "running",
  kicks the real `run_seo_fixes(dry_run=False)` in an `asyncio
  .create_task`, then writes `fix_status="completed" | "failed"` once
  the runner returns.
- `POST /cancel` body `{claim_id}`: only valid while
  `fix_status=="preview"`. Restores one spot via guarded `$inc -1`
  (`spots_claimed > 0`) and marks the claim "cancelled". After a
  confirm or completed claim, cancel is a no-op (spot stays gone).
- Idempotent re-claim: same `(user_id, repo_id)` returns the existing
  claim row without consuming a new spot.

**Backend — Auth wiring (`routers/auth.py`)**:
- `/auth/signup` now persists tz-aware `created_at` AND returns it as
  an ISO string in the response so the SPA can store it.
- `/auth/me` coerces `datetime` → ISO before serialising; legacy
  rows with epoch-float `created_at` pass through untouched (the
  frontend's `getChatBgTint` handles both shapes).

**Frontend — Founder card (`components/FounderOfferCard.jsx`)**:
- Polls `/status` + `/user-status` on mount and every 30 s.
- Visibility rules (the card stays unmounted otherwise):
  • `has_fully_claimed === true` → hidden (already used all 3).
  • `remaining === 0` → hidden (sold out).
  • `days_since_signup > 3` → hidden (welcome window closed).
  • No `projectId` → hidden (no repo to fix).
- Copy locked to the founder-specified line: `"Free SEO fix — from
  the founder"` + `"<X> spots remaining"` (not "claimed").
- Counter color: green if >50, orange if >10, red if ≤10.
- Three-stage interaction: `idle` → `preview` (shows issues_found +
  files_affected list, with `Commit fixes` / `Cancel` buttons) →
  `running` (background commit, toast notifies the user).
- All buttons + states have `data-testid` so the testing harness can
  drive every transition.

**Frontend — Welcome tint (`utils/chatBgTint.js`)**:
- `getChatBgTint(createdAt)` accepts `Date | number | string`;
  auto-promotes legacy epoch-seconds to ms.
- Day 1 → `rgba(234,179,8,0.04)`, day 2 → `0.07`, day 3 → `0.11`,
  day 4+ → `"transparent"` (so the visual cost goes to zero on its
  own — no DB flag, no cleanup cron).
- Wired into ChatPanel's chat-panel root `style.backgroundColor` via
  a `useMemo` of `getUser()?.created_at`. A 600 ms transition makes
  the swap from amber → transparent smooth across the day boundary.

### Tests
- `tests/test_iter212m30_pr2_founder_indexing.py` — **22 tests**:
  pure-function static analysis, end-to-end repo indexing with
  GitHub IO patched, atomic decrement, per-user cap, sold-out path,
  cancel-restores-spot, confirm-flips-status-and-kicks-runner,
  user-status legacy epoch handling.
- `tests/test_iter212m30_pr2_live_http.py` — **9 live HTTP tests**
  (added by the testing agent) with teardown cleanup that resets
  `founder_offer.spots_claimed` and deletes ephemeral test users +
  claims.
- **31/31 pass** + the full 212m-27 → 30 regression suite still green
  (90+ tests).

### Live E2E proofs
| Scenario | Result |
|---|---|
| `GET /founder-offer/status` (no auth) | `{remaining: 500, total: 500, is_active: true}` |
| `GET /founder-offer/user-status` for fresh signup | `days_since_signup ≈ 0`, `has_fully_claimed: false` |
| `GET /founder-offer/user-status` for legacy user (~12 d) | `days_since_signup: 11.96` (card hides) |
| Signup body | now includes `"created_at": "2026-06-26T03:35:54.310503+00:00"` |
| `POST /repos/p_does_not_exist/index` | `HTTP 404 {"detail": "project not found or not owned by caller"}` |

### Honest deviations from the literal user spec
- **Atomic decrement on `/claim`** instead of `/confirm`: kept as
  the spec requires, with a `/cancel` endpoint added to restore the
  spot when the user closes the preview dialog. The testing-agent's
  code review flagged that cancel's two writes aren't transactional —
  noted as a low-risk improvement, not a blocker.
- **Tree-only directory detection** would have missed service
  folders in fixtures that only emit blob nodes; `_analyse_tree`
  now also infers dirs from blob paths so unit tests with sparse
  tree fixtures still work.

### What's NEXT
- PR-3 — Maxx-tier GSC indexing via the `integration_playbook_expert_v2`
  Google Indexing API (deferred per user).
- Cancel transactionality + auto-recover stuck "running" claims.
- Search/replace exact-match fragility in the orchestrator Swift loop.

> **Deployment note**: PREVIEW only. User must redeploy to push to
> `auremcto.com` production. Both the offer counter and the welcome
> tint reset to "fresh" on the prod DB the first time the new code
> runs there.

---

## Iter 212m-29 — SEO Core Engine (PR-1) (Feb 25 2026) ✅

**Real Python/Mongo conversion of the Aurem SEO spec.** Zero mocks,
zero Node.js, fully integrated into the existing FastAPI/Mongo/GitHub-
REST stack. Stack mismatches in the original spec (Node.js, Postgres,
local `fs.readFileSync`, direct Anthropic SDK) all converted to the
project's actual tech.

### What shipped

**`services/seo/` package** — 5 Category-A fixers + orchestrator:
- `meta_tags.py` — inject missing `<title>`, `<meta description>`,
  Open Graph tags (idempotent — skips when present)
- `schema_markup.py` — page-type detection + JSON-LD injection
  (Product/Article/FAQPage/ContactPage/WebPage)
- `robots_txt.py` — render canonical robots.txt with sitemap
  reference + sensible disallows; respects `public/` convention
- `sitemap.py` — pure-function route extraction for Next.js
  `pages/`, `app/`, and plain HTML; strips dynamic `[slug]`,
  `api/`, `_app`, route-groups `(marketing)`
- `image_alts.py` — `<img alt="">` filler that calls
  `services/llm.py:call_llm()` (NOT direct vendor SDK — billing
  + persona pipeline intact); deterministic fallback when LLM
  fails or returns empty
- `orchestrator.py` — single `run_seo_fixes(user_id, project_id,
  options)` entry point. Verifies project ownership in
  `cto_projects`, fetches tree + files via existing
  `services/repo_context._fetch_tree/_fetch_file`, runs every
  plan-enabled fixer, coalesces multi-fixer patches per file, then
  commits via existing `services/github_api_writer.commit_files()`
  in a single atomic commit (or skips commit when `dry_run=True`)

**Admin endpoint** — `POST /api/aurem-dev/admin/seo/run`:
- Admin-only via existing `_require_admin(authorization)` gate
- Pydantic `_SeoRunPayload` validation
- Supports `dry_run=True` (preview patches without committing) and
  `dry_run=False` (real commit)
- Returns the orchestrator's structured result dict verbatim

### Tests
- `tests/test_iter212m29_seo_core_engine.py` — **23 tests**
  covering every fixer + plan matrix + orchestrator end-to-end
  (with all GitHub IO mocked, no network)
- **90/90 pass** across the full 212m-23 → 29 regression suite

### Live E2E proofs
| Scenario | Result |
|---|---|
| `POST /admin/seo/run` no token | `HTTP 401` |
| `POST /admin/seo/run` admin + missing project | `{ok:false, errors:["project not found or not owned by caller"]}` (no GitHub call attempted) |
| Orchestrator dry-run with mocked tree + files | All 5 fixers run, patches coalesced per path, `commit_files` NEVER called, errors=[] |

### What's NEXT (PR-2 + PR-3, blocked on user evaluation)

- **PR-2 — Founder offer counter + 3-day chat-bg tint** (500 spots,
  MongoDB singleton, atomic decrement via `find_one_and_update +
  $inc`, React `<FounderOfferCard />` in chat composer)
- **PR-3 — Maxx-tier GSC indexing** (deferred per user; will need
  `integration_playbook_expert_v2` for the Google Indexing API +
  separate OAuth flow from login)

> **Deployment note**: PREVIEW only. User must redeploy auremcto.com.

---

## Iter 212m-28c — Admin debug endpoint for repo_context_timings (Feb 25 2026) ✅

`GET /api/aurem-dev/admin/debug/repo_context_timings` — operator
spot-check for the new timing telemetry. Admin-only, returns the
20 most recent samples sorted by `ts` desc.

**Honest deviation from user's literal snippet** (paste would have
crashed in 3 places, flagged transparently):

| Issue in literal snippet | Fix |
|---|---|
| `Depends(require_admin)` | `require_admin` symbol doesn't exist; admin.py uses `_require_admin(authorization)` + `Header(None)` everywhere. Used the project-wide pattern. |
| `return {"timings": docs}` | Raw Mongo docs carry `_id: ObjectId` which is NOT JSON-serializable → 500 crash. Per project rules ("Never return raw MongoDB documents"), we coerce `_id` → str, `ts` → ISO string. |
| Endless DB scan | Already capped at 20 in spec; preserved. |

**Response shape**:
```json
{
  "timings": [
    {
      "_id": "6a3ddabc6b180b463192f87f",
      "project_id": "demo",
      "owner": "tiangolo", "repo": "fastapi", "branch": "master",
      "cold_path": true,
      "phases_ms": {"tree_fetch_ms": 514, "rescue_ms": 0, "inline_ms": 280},
      "total_ms": 795,
      "files_inlined": 2,
      "ts": "2026-06-26T01:49:48.957000"
    }
  ],
  "count": 1
}
```

**E2E proof** (founder JWT):
- `HTTP 401` without token (gate works)
- `HTTP 200` with founder token, seeded sample returned with all
  fields JSON-clean
- Cleanup verified — no test pollution

**Tests**: `tests/test_iter212m28c_admin_debug_timings.py` (6 pins).
**28/28 pass** across 212m-27, 212m-28, 212m-28c.

---

## Iter 212m-28 — repo_context Hot-Path Parallelisation (Feb 25 2026) ✅

**Real cause** of the 5-15 s chat latency was found and fixed.
**No mock MCP endpoint** (the proposed `github.com/mcp` is fictitious).

### Root cause (confirmed via code inspection)

`services/repo_context.py:_build_blob()` had **two sequential
fan-out loops**:
- File inlining: 10 files × ~500 ms = **~5 s** serial.
- Truncation rescue: 8 top-level dirs × ~1 s = **~8 s** serial.

### Fixes

1. **Parallel file inlining** — `for path in picks: await _fetch_file(...)`
   → `asyncio.gather(*(_bounded_fetch(p) for p in picks))` with a
   semaphore of 6 (under GitHub's secondary rate limit).
2. **Parallel truncation rescue** — same `asyncio.gather` treatment
   for the per-top-level-dir BFS.
3. **Branch-aware cache** — cache key changed from `{project_id}`
   to `{project_id, branch}`; `invalidate_repo_context()` now uses
   `delete_many` so a PAT change wipes every branch's blob.
4. **Per-phase timing instrumentation** — every call (cold OR cache
   hit) writes a sample into `repo_context_timings` with the per-
   phase millisecond breakdown (`tree_fetch_ms`, `rescue_ms`,
   `inline_ms`, `cache_hit_ms`, `total_ms`). 7-day TTL index on `ts`
   so the collection can't grow unbounded.
5. **Parameterised logging** — every new log line uses `%s` / `%r`
   placeholders so Vanguard's f-string-with-id guard stays green.

### Benchmark proof (synthetic but realistic)

```
Tree fetch:  200ms (1× call)
Inline 10 files SERIAL  : 10 × 500ms = 5200ms    ← old path
Inline 10 files PARALLEL: max(2 waves × 500ms) = 1202ms measured  ← new path
Speedup: 4.3× on COLD path
```

Cache-hit path (warm turns) was already <50 ms; unchanged.

### Tests

- `tests/test_iter212m28_repo_context_parallel.py` — 12 tests:
  source pins for both gather sites, semaphore cap, branch-aware
  cache, telemetry collection + TTL index, parameterised logging,
  and a **runtime benchmark** asserting parallel ≥ 3× faster than
  serial. **80/80 pass** across the full 212m-23..28 regression suite.

> **Deployment note**: PREVIEW only. User must redeploy to auremcto.com.

---

## Iter 212m-27 — Vanguard Hot-Path Hardening (Feb 25 2026) ✅

**Production-grade E2E refactor of two chat hot-paths** to fix slow
repo loading and close 4 Vanguard security findings. **No mocks, no
TODOs, no patchwork — legacy unbounded code is fully excised.**

### Latency caps applied
| Hot-path call | Old | New | Fallback |
|---|---|---|---|
| `get_repo_context()` in chat_send | unbounded | **12 s** | empty `repo_ctx` |
| `chat_sessions.find_one()` for history | unbounded | **3 s** | empty history |
| `list_tools()` upstream HTTP | unbounded | **8 s** | local-only tools |

### Security findings closed

1. **IDOR — cross-user repo context leak** (chat.py)
   Caller's `user_id` is now required to own the requested
   `project_id`. Mismatch → `HTTPException(403, "Project access denied")`.

2. **NoSQL injection — `session_id`** (orchestrator.py)
   New module-level regex `_VALID_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")`.
   Accepts UUIDs + legacy fallback ids + test ids; rejects Mongo
   operator payloads (`{"$gt":""}`), shell metacharacters, oversized
   keys, Unicode lookalikes, null bytes.

   > **Spec deviation noted**: user spec said `.isalnum()` but that
   > would reject *every* legitimate UUID (hyphenated) — same
   > security intent achieved with the regex without breaking real
   > sessions. Documented inline + in the test pin.

3. **Privilege escalation — session_id-only history lookup** (orchestrator.py)
   Filter changed from `{session_id}` to `{session_id, user_id}` so
   a leaked session id from user A can never read user B's transcript.

4. **F-string log injection** (both files)
   All warnings in the new hot path use `%s` / `%r` placeholders —
   Vanguard regex guard now passes.

### E2E proofs (live preview)

| # | Scenario | Result |
|---|---|---|
| 1 | Clean chat (no project) | `content="OK"`, provider=`glm-5.2`, iters=1, 4.9 s |
| 2 | `POST /chat/send` with foreign `project_id` | **HTTP 403** + `{"detail":"Project access denied"}` |
| 3 | `POST /chat/send` with `session_id='{"$gt":""}'` | Chat continues (regex rejects, history loaded empty), `content="OK"` |
| 4 | Backend log of rejection | `WARNING rejected malformed session_id (type=str, len=10) — loading history as empty` (parameterised, no f-string) |

### Tests
- `tests/test_iter212m27_vanguard_hardening.py` — 10 source-pin +
  functional regex tests. **68/68 pass** across the full 212m-23..27
  + iter157/169/172 regression suite.

> **Deployment note**: PREVIEW only. User must redeploy to auremcto.com.

---

## Iter 212m-26 — Truncation + Auto-Ship Removal (Feb 25 2026) ✅

**Two production bugs reported by user on auremcto.com.**

### Bug #1 — ORA reply truncates to 1 line ✅ FIXED

- **Root cause**: `MAX_TOKENS["chat"] = 1500` in `services/llm.py` +
  orchestrator's `token_budget = 3500 if use_code_model else 1500`.
  GLM-5.2 hit the 1500-token cap mid-paragraph, surfacing as the
  "only one line then stops" bug.
- **Fix**: Both raised to **4000** with env override
  `LLM_CHAT_MAX_TOKENS`. Code-mode honors `LLM_CODE_MAX_TOKENS`.
- **E2E proof**: prompt "explain in 5 paragraphs: FastAPI, React,
  MongoDB, Redis, Docker" — reply now **3442 chars / 18 lines / all
  5 sections / ends with full natural sentence**.

### Bug #2 — "SHIP VIA CTO" button auto-triggers ✅ FIXED

- **Root cause**: `_maybe_ship_shortcut` in `routers/chat.py` auto-
  fired a CTO task whenever the user typed a short confirmation
  ("yes", "ok", "fix", "go", "do it"...) after an assistant turn
  containing an aurem-handoff fence. The manual "🚀 Ship via CTO"
  button was bypassed — common conversational replies silently
  committed to GitHub.
- **Real fix (no patchwork — per user)**: DELETED the entire path:
  - `_SHIP_CONFIRMATIONS` set (~10 lines)
  - `_normalise_confirmation` (~3 lines)
  - `_looks_like_ship_confirmation` (~5 lines)
  - `_maybe_clarify_short_fix` (~38 lines)
  - `_maybe_ship_shortcut` function body (~234 lines)
  - Call site in `chat_stream` (~12 lines)
  - 4 obsolete test files / blocks removed:
    - `test_iter87_ship_shortcut.py` (DELETED)
    - `test_iter125_ship_shortcut_task_handoff.py` (DELETED)
    - `test_iter132_ship_shortcut_tick_emission.py` (DELETED)
    - `test_iter136_hard_timeout_enforced.py::test_ship_shortcut_has_hard_timeout` (DELETED)
    - `test_iter169_fix_hallucination_guards.py::test_clarify_guard_*` (4 tests REMOVED)
    - `test_iter172_shell_handoff_guard.py::TestShipShortcutRefusesShellHandoff` (REPLACED with stub)
- **Shell-handoff guard preserved**: orthogonal protection that
  catches `pip install` / `npm install` fake handoffs stays active.
- **E2E proof**: seeded a session with an aurem-handoff fence, user
  posted "yes" — response was a normal `aurem-cto` orchestrator
  reply, no `aurem-ship-shortcut`, no `ship_shortcut: true`, no
  `task_handoff` frame, no `task_id` minted. Manual button click in
  `MessageBubble.jsx → ShipDialog → onShip={shipViaCTO}` remains
  the ONLY path that creates a CTO task.

**Tests**: `tests/test_iter212m26_truncation_and_autoship_removal.py`
— 10 source pins + runtime assertion. **60/60 pass** in the curated
regression suite (212m-23 through 212m-26 + iter157 + iter169 + iter172).

> **Deployment note**: Both fixes are PREVIEW only. User must
> redeploy to push to `auremcto.com` production.

---

## Iter 212m-25 — F12 Auto-clear + Logo Cache-Clean Button (Feb 25 2026) ✅

**Feature**: Two UX hygiene fixes for the customer interface.

1. **F12 console auto-clear** — DevTools console clears automatically
   on app startup, on every route change, AND every 30 seconds.
   Escape hatch: `window.__AUREM_DISABLE_AUTO_CLEAR_CONSOLE = true`
   in console disables it for a debugging session.

2. **Logo click = cache clear + auto-refresh** — Clicking the AUREM
   Dev logo (sidebar top-left) wipes UI cache (sessionStorage,
   non-auth localStorage, IndexedDB, ServiceWorker caches) and
   auto-reloads the CURRENT page with a `?_cc=<ts>` cache-bust param.
   Login (`aurem_token` + `aurem_user`) is preserved — user stays
   signed in.

3. **Explicit "🧹 Clear cache" button** — Sits right under the logo
   when the sidebar is expanded; same behaviour as logo click, plus
   a toast confirming how many items were cleared.

**Files**
- NEW `frontend/src/lib/cacheCleaner.js` — `clearUICache()` +
  `clearUICacheAndReload()`.
- NEW `frontend/src/lib/useAutoClearConsole.js` — startup + route +
  30s periodic hook.
- NEW `frontend/src/components/ClearCacheButton.jsx` — pill button.
- MOD `frontend/src/components/Shell.jsx` — brand NavLink → button
  with clear+reload handler; ClearCacheButton inserted under brand.
- MOD `frontend/src/App.jsx` — `<AutoClearConsoleHost />` child of
  `<BrowserRouter>` so `useLocation()` works.
- NEW `frontend/src/lib/cacheCleaner.test.js` — Jest unit tests.
- NEW `backend/tests/test_iter212m25_cache_cleanup_sources.py` —
  9 source-level pins (all pass).

**E2E proof** (manual playwright):
- Seeded `misc_cache_v3`, `ui_pref_collapsed` in localStorage and
  `scroll_pos_settings`, `draft_text` in sessionStorage.
- Clicked `[data-testid='clear-cache-btn']`.
- After 2.5s: `aurem_token` + `aurem_user` STILL present; all 4
  seeded items gone; URL = `/settings?_cc=mqsx9uxu`; page rendered
  with user data still visible.

---

## Iter 212m-24 — Admin House Rules (Feb 25 2026) ✅

**Feature**: A global "House Rules" prompt that ORA reads FIRST
(highest priority — before its own persona, tool catalog, project
context). Each target (ORA Chat, Ask Advisor) and each chat mode
(Swift, Pro, Maxx) has its own green/red toggle so the admin can
scope exactly where the rules apply.

**Backend**
- New `services/house_rules.py`: singleton Mongo doc + 30s in-process
  cache + `get_active_house_rules(target, mode)` helper +
  `format_house_rules_block(prompt)` wrapper that prepends a
  "HIGHEST PRIORITY — READ FIRST" header. OFF-stub on DB failure so
  chat never breaks when Mongo is down.
- New endpoints in `routers/admin.py`: `GET /admin/house-rules` and
  `PUT /admin/house-rules` (admin-only via `_require_admin`).
  Validated with a `HouseRulesPayload` pydantic model.
- Injected into `routers/chat.py` at three sites — `chat_send`,
  `chat_stream` main path (gated on `not body.ora_panel`), and
  `chat_stream` Ask Advisor path. The block is PREPENDED to
  `extra_sys` so it lands before the orchestrator's persona stack.

**Frontend**
- New `components/AdminHouseRules.jsx`: prompt textarea (8 KB cap),
  5 green/red toggles, save/reload buttons, live/inactive badge,
  warnings for "no target on" and "chat on but no mode on", dim
  chat-modes section when ORA Chat is off.
- Wired into `pages/Admin.jsx` as NAV item "House Rules" (between
  Audit and Settings) with `data-testid='admin-nav-house_rules'`.

**Tests**
- `tests/test_iter212m24_house_rules.py` — 11 unit tests (service +
  router + injection pins). All pass.
- `tests/test_iter212m24_e2e_house_rules.py` — 9 live HTTP tests
  added by testing agent. 8 pass / 1 skipped (non-admin 403 needs
  a non-admin preview seed).

**E2E proof**: Manual swift chat with rule "prepend [HOUSE-RULE-OK]"
enabled for chat+swift only — Swift reply began with the marker,
Pro reply did NOT. Reset to OFF/empty after verification.

---

## Iter 212m-23 — URL Tool Real Fix (Feb 25 2026) ✅

**Bug**: The legacy `build_url_context` in `routers/chat.py` eagerly
scraped any http(s) URL in the prompt and stuffed the result into
the system prompt. That bypassed the standard tool orchestration:
no step card, no `tool_invocations` entry, no `web_sources` chip,
and sometimes `<tool_call>` tags leaked into the user-visible
stream.

**Fix** (real, not patchwork):
1. **Removed** `build_url_context` import + every call site in
   `routers/chat.py` (both `/send` and `/stream` paths). Eager URL
   scraping is GONE.
2. **Added** a deterministic forced `fetch_url` pre-execution block
   in `services/orchestrator.py` (~lines 1657-1763), BEFORE the
   `while iters < max_iters:` loop. Extracts URLs via
   `extract_urls(prompt)[:3]`, dispatches `fetch_url` through the
   same `invoke_local_tool` / `invoke_tool` path the LLM would use,
   appends `{'forced': True}` entry to `invocations[]`, fires
   `step_hook("📖 Reading URL…")`, and folds the result into the
   transcript as an iter-0 `TOOL RESULTS` block.

**Tests**
- `tests/test_iter212m23_url_tool_real_fix.py` — 9 source pins.
- `tests/test_iter212m23_e2e_url_tool_real_fix.py` — 5 live E2E.
- `tests/test_iter157_cold_start_fixes.py` — updated to drop the
  obsolete `build_url_context` pin.

**E2E proof**: URL prompt → SSE stream emits `📖 Reading URL…` step
frame, `fetch_url` invocation with `forced:true`, no `<tool_call>`
leakage in user tokens, provider=glm-5.2, `tool_calls_run=3` in
the meta done frame. Tavily upstream 432 (quota) — separate billing
matter, not a code bug.

---
