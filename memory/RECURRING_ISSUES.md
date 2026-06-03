# RECURRING ISSUES — AUREM CTO worker pain log

> **Purpose**: This file lives forever. Any agent that joins this codebase
> MUST read it before touching the ORA orchestrator, Vanguard validator,
> Mode-D root-cause analyzer, or anything to do with multi-file repo work.
>
> **Promise to the user (Teji)**: Each of these patterns has bitten you at
> least twice in production. We log them here so the next agent doesn't
> waste your tokens (or your patience) re-discovering them.

---

## Recurrence Pattern #1 — "empty file body" rejection loop

**What the user sees** (verbatim, multiple sessions):
> ❌ Task failed — nothing was committed.
> Error: AI returned suspect edits (refusing to push):
>   - backend/pillars/command_hub/worker.py — empty file body

**Root cause**: ORA's Two-Agent Maxx writer (DeepSeek) returns a JSON
patch where the target file's `content` field is `""` or a single
docstring of <40 chars. Vanguard's pre-commit gate (correctly) refuses
to push that empty body. ORA then retries with the **exact same prompt
structure** and produces the same empty body, looping the user.

**Why it loops**: the failure feedback ("empty file body") is **not fed
back into the model's next-turn prompt**. ORA just retries the same
plan.

**Fix locations**:
- `/app/backend/services/vanguard.py` — emits the "empty file body"
  verdict. When this verdict fires, **the orchestrator must inject the
  rejection reason + the previous (empty) content into the retry
  prompt** so the model sees what it failed at.
- `/app/backend/services/orchestrator.py` — currently passes a generic
  "regenerate" instruction. Patch: include `last_vanguard_verdict` in
  the retry user message.
- `/app/backend/services/llm_writer.py` (or equivalent) — add a
  client-side guard: if response includes `"content": ""` for a file
  the planner said needs N classes/functions, **mark it as a failed
  generation locally and retry once with an explicit "the body must
  contain a class/function definition, not just a docstring" hint** —
  before sending to Vanguard.

**Until fixed**, **Mode F (Engage)** should be temporarily disallowed
from writing skeleton multi-file scaffolds in one shot. Break them into
1-file-per-turn, which sidesteps the loop.

---

## Recurrence Pattern #2 — "90s timeout streamer" firing on barely-used budget

**What the user sees**:
> ⏱️ I cut myself off at 90s to avoid a runaway tool-loop.
> Tools used: read_repo_files (1 calls).

**Root cause**: The timeout is `90s` wall-clock from the SSE stream
start. When DeepSeek is slow to first byte (cold start / OpenRouter
queue / Cloudflare blip), the 90s budget is consumed by **waiting**,
not by tool-loops. ORA reports "ran out of reasoning steps" even though
it only made 1 tool call. The message is **misleading** — user
correctly concludes "you're not even trying".

**Fix locations**:
- `/app/backend/services/orchestrator.py` — split the 90s into
  `time_to_first_token` (max 30s, hard) + `time_after_first_token`
  (max 90s for the actual reasoning loop). Already partially fixed in
  Iter 55 ("90s timeout streamer" feature) but the user-facing
  message still reads as if ORA ran a 90s loop.
- **Fix the message**: when timeout fires with `tool_calls < 3`, the
  human-readable summary must read "The model API was slow to respond
  (waited 90s for first byte)" — NOT "I cut myself off".
- Add `network_ttfb_ms` to the timeout payload so the wrap-up message
  can distinguish slow-API from genuine tool-loop.

---

## Recurrence Pattern #3 — Mode D returns boilerplate for missing-signal cases

**What the user sees**:
> 🟢 Root cause: insufficient signal to diagnose.
> Fix: Reproduce the error with a real stack trace or 4xx/5xx HTTP status.
> Files to check: (none)

…when the user has already explained the problem ("Pillar 4 broken,
0 live workers, 19 collections, status=Broken"). The diagnosis is
**actually possible** from the screenshot + repo source, but Mode D
hard-bails because no stack trace is attached.

**Root cause**: `/app/backend/services/mode_d_debug.py` (or wherever
the diagnosis prompt lives) has a too-aggressive "signal threshold"
that demands a literal HTTP error code or Python stack trace before
returning a useful answer. Symptoms in natural language ("0 live
workers", "Broken status badge") don't pass the threshold.

**Fix locations**:
- Lower the "insufficient_signal" threshold to accept screenshots +
  numeric symptoms as valid signal.
- When the user says "broken" but no stack trace, **fall back to Mode
  A** (read repo files + analyze) instead of bailing with the canned
  message. Mode D's "insufficient signal" reply should ONLY fire when
  the user message itself is ambiguous (<20 chars, no nouns).

---

## Recurrence Pattern #4 — Wrong-mode classification for repo-info queries

**What the user sees**: Asks _"is repo main kitni total files hain
and kitni count of total lines coading hai"_ (in repo X how many files
total and how many lines of code) → ORA routes to **Mode D debug** and
returns:
> 🟡 A network request was aborted mid-stream…
> Files to check: https://auremcto.com/assets/index-BihldfRE.js

…which is a **completely fabricated** diagnosis derived from a
URL fragment in an earlier message in the conversation, not from the
repo at all.

**Root cause**: The mode classifier in
`/app/backend/services/mode_classifier.py` (or the LLM router) is
matching on the word "abort"/"aborted" in unrelated context. The
user's question contains "coading" (= "coding"), which doesn't trigger
Mode A's repo-stats branch.

**Fix locations**:
- Mode classifier must have an explicit **"repo metrics / stats /
  count / size / lines"** intent → Mode A with `tool=list_repo_files`
  + a programmatic line count, NOT an LLM diagnosis.
- Add a Hinglish + transliteration tolerance pass before classifying
  (e.g. "coading" → "coding", "kitni" → "how many").
- Never quote a URL from prior conversation as a "file to check" in
  Mode D output. The "Files to check" field must be sourced from the
  current turn's tool calls, not history.

---

## Recurrence Pattern #5 — Multi-pillar work always finishes 1-of-N then "Next:..."

**What the user sees**: Asks _"create 4 pillar workers + health.py"_ →
ORA ships **1 file**, says _"✅ Created sales worker. Next: implement
remaining 2 workers and health.py"_, user has to retype the request,
ORA ships another file, says "Next:..." etc. Took **6 round-trips**
to complete a task that could've been 1 commit.

**Root cause**: The orchestrator's plan/execute split has a **hard
file budget of 2 files per `cto_tasks` row**. This was originally a
safety rail. For multi-file scaffolding tasks it produces death-by-
1000-cuts UX.

**Fix locations**:
- `/app/backend/services/orchestrator.py` — when the user prompt
  contains "create N files", "all 4 workers", "every", "scaffold",
  raise the file budget for that single task to 8 (the Vanguard scan
  budget is independent).
- After the budget raise, batch ALL planned files in one commit.
  Vanguard runs once over the whole batch.

---

## Recurrence Pattern #6 — Stale build / browser cache on production

**What the user sees**: "last 2-3 deploys ka theme nahi dikh raha".

**Verified Jun 2026**: the production bundle DID contain the latest
changes (proved via grep on `index-BihldfRE.js`). The user's BROWSER
had cached the previous bundle. Cloudflare cache `max-age=300` means
even Cloudflare clears in 5 min — only the user-side service-worker /
disk cache held the stale build.

**Mitigation already shipped** (Iter 63):
- `/app/admin` → "🧹 Purge & hard-refresh" button. Server-side purges
  Cloudflare + Mongo + LRU caches. Client-side unregisters SWs +
  blows `caches.delete()` + reloads with `?_purge=<ts>` bust.

**Open follow-up**:
- Show **current bundle hash + build timestamp** at the top of the
  admin overview so the user can instantly self-diagnose
  "am I on the right build?" without grep'ing the bundle.

---

## Standing rules for any agent touching ORA / Vanguard / Mode-D

1. **Never** silently retry an empty-body failure. Surface the rejection
   reason in the next prompt.
2. **Never** report "I cut myself off at 90s" if tool calls < 3.
   Distinguish slow-API from genuine loop in the wrap-up message.
3. **Never** quote URLs from the conversation history as
   "files to check" in Mode D. Sources must come from this turn's tool
   calls.
4. **Always** classify intent BEFORE picking a mode. "How many files /
   lines / size" → Mode A repo-metrics, not Mode D debug.
5. **Never** ship a partial multi-file scaffold without telling the
   user explicitly: _"I shipped X of Y planned files; reply 'next' to
   continue"_ AND adding the remaining files to a follow-up task row
   so the user does not have to retype.
6. When the user expresses frustration ("you're useless", "complaint
   karunga", "fix kro yaar"), **stop the current plan, summarize what
   went wrong honestly in ≤3 lines, propose the smallest possible
   working scope, and ask for go-ahead.** Do not respond with another
   apologetic restatement of the same broken plan.

---

_Last updated: Feb 2026 — by E1 fork agent at the user's explicit
request after pattern recurrence in Pillar 4 / command_hub work._

---

## Iter 67 — Patch landed

| Pattern | Status | Fix location |
|---|---|---|
| #1 — Empty file body retry loop | **PARTIAL FIX** — retry endpoint now passes previous error + explicit "write FULL implementation" hint into new task's `context`. In-task auto-regenerate before failing still TODO (deeper orchestrator surgery — deferred). | `backend/routers/cto_projects.py::retry_task` |
| #2 — 90s timeout misleading message | **FIXED** — when `tool_count < 3`, message reads "Model API was slow", `slow_api: true` flag in meta payload. | `backend/routers/chat.py` ~line 847 |
| #3 — Mode D boilerplate for missing-signal cases | **FIXED** — Mode D system prompt now lists valid signals (natural-language symptoms, screenshots, F12 errors, file_contents) and explicitly tells the model to "prefer a Mode-A-style READ plan over bailing" when signals are weak. | `backend/services/mode_d_debugger.py::DIAGNOSIS_SYSTEM` |
| #4 — Wrong-mode classification | **FIXED** — new `services/mode_classifier.py` adds `classify_intent_v2()` with confidence scores + `needs_confirm` flag. Wired into `routers/chat.py` so SSE `mode` event now carries `confidence`, `scores`, `needs_confirm`. UI can ask user when ambiguous. | `backend/services/mode_classifier.py` + `routers/chat.py` |
| #5 — Multi-file scaffold 1-of-N | **VERIFIED NO HARD CAP IN CODE** — `_run_task` doesn't limit edits count. The 1-of-N pattern is from the LLM's own self-limiting. Deferred — needs prompt engineering, not codebase change. | n/a |
| #6 — Stale browser cache | **FIXED** (Iter 63) — admin panel "🧹 Purge & hard-refresh" button. | `frontend/src/pages/AdminOverview.jsx` |

Regression-locked by `backend/tests/test_iter67_recurring_pattern_fixes.py` (3 tests).
