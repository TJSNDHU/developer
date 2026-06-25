# AUREM Dev / Aurem CTO — Changelog

Append-only iteration log. See `PRD.md` for the original problem
statement and historical context; this file captures recent feature
work in date-stamped chunks so PRD.md stays focused.

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
