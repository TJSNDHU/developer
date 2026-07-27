# Iter 318+ — Data-Loss / Ship-Integrity / Trust-Visibility Spec

**Status:** REQUIREMENT ONLY, NOT BUILT. Handoff from context boundary 2026-07-27 ~05:10 UTC.
**Priority:** #5 (data loss, still live in prod) → #1 (SSE Iter-317 re-validation) → #2 (LoopStepBar sync) → #4 (reload rehydration) → #3 (console.clear).
**Standing rules:** No mocks, one class per deploy, test-first repro, `bug_testing_agent` verification, standalone deploy authorization, stop-and-report on hidden depth, no self-authorized deploys, no partial fixes to P0 data-loss.

---

## Evidence base (2026-07-27 founder live-inspection, loop_678eea28436c4e)

- `ship_pending.files["README.md"]` ended with literal string `[Rest of existing README content remains unchanged...]` as actual file content.
- Original task: "add one comment line at the top of README.md."
- README dropped from full multi-thousand-word document to ~21 lines of ToC + the placeholder marker.
- **Reduction: >90%.**
- Commit would have wiped everything below the ToC. Only `awaiting_confirmation_timeout` prevented ship.
- `verification_results.ok = true` because `.md → linter: skip`. Skip masqueraded as pass.
- `scan_results = NameError("name '_scan_text' is not defined")` — scan phase hard-crashed, loop treated it as non-fatal, proceeded to ship-gate.
- Backend `_emit` timestamps from admin inspect: `planning 04:30:55.219 → awaiting_confirmation 04:31:16.815`. Δ = 21.6s plan latency (separate item, not this spec).
- **SSE STILL BROKEN post-Iter-317-deploy candidate:** Iter 317 deployed 05:04, but the 04:30 loop predates it. Next same-class run's console log settles whether Iter 317's SSE-gzip-exclusion fix actually worked. **Do NOT declare Iter 317 successful without console-log evidence from a post-05:04 same-class loop showing NO `[iter316] FALLBACK-POLL delivered plan ... (SSE path did NOT deliver first)` line.**

---

## Bug 1a — Executor placeholder/elision emission ban (BLOCKING PRECONDITION, NOT STRETCH)

**Founder-mandated status:** *"1a closes the actual defect, 1b/2 is the safety net under it. Write the spec so a future agent can't accept a PR that ships 1b/2 alone and calls it done."*

**Primary defect:** Executor generates file content containing elision markers instead of the full file body OR a valid patch. This is the root cause. Guard-only fixes are second-layer catch that fails when the marker vocabulary drifts.

**Fix location:** Likely `services/loop_engine.py::_do_execute` or the executor helper it calls (search for where `ship_pending.files[<path>]` is written). The LLM prompt for execute-phase likely permits or encourages "for brevity, show only changed regions" — must be rewritten to demand FULL file body OR unified diff.

**Test invariants (all MUST hold before merge):**
1. **Repro test (must FAIL against current code):** Feed the executor a synthetic prompt that historically produces elision (e.g., "add one comment to a large README"). Assert the resulting `ship_pending.files[<path>]` contains ZERO elision markers. Currently it will contain one → fail.
2. **Prompt-inspection invariant:** grep the execute-phase system prompt for tokens like "unchanged", "brevity", "abbreviated", "..." — if present, prompt is at fault. Assert their absence post-fix.
3. **Regression: full-file emission still works.** For a task that legitimately rewrites a file, the executor must still produce the complete new body, not an empty file or a diff.

**Acceptance:** PR that ships only 1b/2 without 1a is REJECTED at review time. Reviewer note the reviewer to enforce this: "1a is the ban on emission at source. 1b/2 is defense in depth. Both required or neither ships."

---

## Bug 1b — Pre-ship guard (SAFETY NET, DOES NOT REPLACE 1a)

**Fix location:** `_do_ship` or equivalent — the code path that receives `ship_pending.files` and hands to GitHub commit.

**Guard rules (all must fire independently):**

**Rule 1: Elision-marker regex sweep.** Reject if any submitted file body matches any of:
- `\[Rest of .*(unchanged|remains|goes here|omitted|elided|truncated)\]`
- `\.{3,}\s*(unchanged|snip|omitted|remainder)`
- `<!--\s*(snip|elided|truncated|unchanged|omitted)\s*-->`
- `//\s*\.\.\.\s*(unchanged|remainder|omitted)`
- `#\s*\.\.\.\s*(rest|remainder|unchanged|omitted)`
- `/\*\s*(snip|elided|unchanged)\s*\*/`
- `\{\{\s*(rest|remainder|unchanged)\s*\}\}`

Regex list is deliberately non-exhaustive because vocabulary drifts — this is why 1a must ship.

**Rule 2: Size-delta guard.** Reject if `len(submitted_body) < 0.30 * len(repo_body)` UNLESS `original_request` (verbatim founder prompt) contains an explicit deletion/replacement intent (regex: `\b(delete|remove|empty|clear|wipe|rewrite from scratch|replace entirely|start over)\b`).

**Concrete threshold rationale (from evidence, don't re-derive):** This run's README dropped >90% of its bytes. A 30% floor (i.e., flag any shrink >70%) is defensible from this single data point. If review wants to be more conservative, 50% floor (flag any shrink >50%) is the upper bound of the reasonable range. **Do NOT go below 20% floor / above 80% shrink tolerance** — that lets this exact bug through.

**Rule 3: Byte-count sanity for non-deletion actions.** If `plan.files[<path>].action == "edit"` (not "delete"/"replace_full"), require `len(submitted_body) >= 0.30 * len(repo_body)`. Redundant with Rule 2 but catches cases where original_request is ambiguous.

**On any rule triggering:** Loop transitions to a NEW distinct terminal state (proposed: `failed_integrity_guard`). Error blob attaches: `{rule_fired: "elision_marker"|"size_delta"|"byte_count", offending_path, offending_marker_text (redacted to first 80 chars), submitted_bytes, repo_bytes, shrink_ratio}`. Founder sees an explicit "ship blocked: <reason>" message, NOT the generic "verify failed."

**Test invariants:**
1. Repro: submit a file body with `[Rest of existing README content remains unchanged]` → assert `failed_integrity_guard`, elision_marker rule.
2. Repro: submit a file body that's 5% of repo size for an "edit" action → assert `failed_integrity_guard`, size_delta rule.
3. Regression: submit a legit small file for `action=delete` where original_request says "delete X.md" → assert allowed through.
4. Regression: submit a legit growing file body → assert allowed through.

---

## Bug 2 — Verify-phase skip ≠ pass (BUNDLED WITH 1b, SAME CLASS)

**Founder mandate:** "a skipped linter should still run the size-delta and elision-marker checks."

**Fix location:** `_do_verify` — currently sets `verification_results[<path>].ok = true` when linter is skipped (unknown extension, .md, etc.). Must instead: run Rule 1 (elision-marker) and Rule 2 (size-delta) from Bug 1b, THEN set `ok` based on those.

**Test invariants:**
1. `.md` file with elision marker → verify returns `ok: false, reason: "elision_marker"` (currently returns `ok: true`).
2. `.md` file legitimately shrunk 5% for a formatting change → verify returns `ok: true` (regression).
3. `.py` file with linter running normally → linter result is authoritative, size-delta is ADDITIONAL check (both must pass).

---

## Bug 3 — Scan-phase NameError + fail-closed (SEPARATE ITER, likely Iter 319)

**Evidence:** `scan_results = NameError("name '_scan_text' is not defined")` in `loop_engine.py:2908` and `:3030`. Lint flagged this earlier (F821), main-agent noted it as "pre-existing, unrelated to Iter 315" — that was wrong. It is a real production bug.

**Fix location:** Define `_scan_text` (or fix the broken reference — inspect surrounding code to determine which function should be called; grep for `def _scan_` in same file).

**Fail-closed contract:** Wrap `_do_scan` in `try/except`. On ANY exception (NameError, LLM timeout, whatever): loop transitions to `failed_scan_exception`, ship is halted, NOT pass-through. Current behavior treats scan as non-fatal — that is the wrong default for a security scanner.

**Test invariants:**
1. Repro: inject a `raise RuntimeError` into `_do_scan` → assert loop enters `failed_scan_exception`, ship-gate does NOT open.
2. Regression: normal scan pass → loop proceeds to ship-gate as before.
3. Fix the NameError, verify with `python -c "import services.loop_engine"` — no NameError at import; a lint pass showing F821 cleared.

---

## Bug 4 — Reload-rehydration missing paused_for_user + ship_pending branch (HYPOTHESIS, NOT CONFIRMED)

**Founder-flagged hypothesis to VERIFY, not asserted diagnosis:**

> "check whether Iter 316's hydrate handler has a state-machine switch that's missing this branch"

**Symptom (confirmed live):** Reload of `/dashboard` during a `state=paused_for_user, ship_pending=set` loop wipes the chat panel, ship-ready card, commit message, and file list from the UI. Backend still has the record (visible in `/admin/inspect-loop`). No UI path back to it.

**Verification steps for next-context agent (BEFORE writing any fix):**
1. Read `frontend/src/components/ChatPanel.jsx` hydrate branch around line 500-530 (this is where Iter 316 Fix B added `awaiting_confirmation + plan` handling).
2. Check whether the branch has an explicit case for `active.state === "paused_for_user"` when `active.ship_pending` exists — Iter 316 Fix B may have handled only the plan-approval variant, leaving the ship-approval variant orphaned.
3. If confirmed missing: fix is same pattern as Iter 316 Fix B (setLoopId + setLoopPhase + openLoopStream + render the ShipPendingCard).
4. If the branch exists but doesn't render: hypothesis refuted, real cause is elsewhere (redux/state-store staleness, race with WrappedContext load, etc.) — investigate further before coding.

**DO NOT anchor on the "Iter 316 missed a branch" theory** without step-1/2 verification. Founder was explicit: "flag it as hypothesis to verify, not confirmed shared root cause."

---

## Bug 5 — console.clear() app-wide 30s timer (DEBUGGING HYGIENE, LOWEST PRIORITY)

**Evidence:** `index-DtRTF4-t.js` calls `console.clear()` at `:29:59, :30:00, :30:31, :31:00, :31:30 ...` — a repeating 30s timer wiping the DevTools console.

**Verification:** grep frontend build for `console.clear`. If it's inside the F12 error-capture code, it's likely intentional (the F12 badge captures errors first, then clears). If it's in third-party code (unlikely), track it down.

**Founder ask:** "confirm this is intentional." If yes → add a `DISABLE_CONSOLE_CLEAR` env-flag so future debug sessions can opt out. If no → remove.

---

## Iter 317 SSE re-validation (WATCH ITEM, NOT AN ITER)

Iter 317 shipped SSE-gzip-exclusion middleware. Loop `loop_678eea28436c4e` predates the deploy so cannot validate. On the NEXT same-class run (same-size prompt, same repo scale), inspect browser console:

- If `[iter316] SSE PLAN-READY FRAME arrived` line appears within ~2s of `awaiting_confirmation` timestamp visible in admin inspect → Iter 317 worked, SSE delivering.
- If `[iter316] FALLBACK-POLL delivered plan ... (SSE path did NOT deliver first — investigate)` appears again → Iter 317 was NOT the fix. Gzip was wrong. Real transport bug still open. Next hypotheses to investigate (in order):
  1. Pod-replica routing (multiple k8s replicas, SSE client connects to different pod than engine — Mongo fallback catching it at 2s poll cadence)
  2. Uvicorn buffering / ASGI send-flush edge case
  3. Client-side EventSource reconnect logic dropping the initial event

---

## Iter-numbering after this spec

Sequence (locked, do not reorder without founder ack):
1. **Iter 318** — Bug 1a + Bug 1b + Bug 2 (bundled, data-integrity class). MUST include ALL THREE. 1a is not a stretch goal.
2. **Iter 319** — Bug 3 (scan NameError + fail-closed).
3. **Iter 320** — Bug 4 (reload rehydration — after verification step confirms Iter 316-hydrate-branch hypothesis).
4. **Iter 321** — Bug 5 (console.clear investigation).
5. **Item 1 continuation** — plan-phase latency profiling (21.6s for one-line edit is too much). Speed-diagnostic data on a post-Iter-315 loop identifies whether it's LLM call or repo grounding.

Do NOT bundle across iters. Data-loss (318) cannot ship with anything else. Scan (319) is a distinct class.

---

## Handoff checklist for next-context agent

Before starting Iter 318:
- [ ] Read this file entirely.
- [ ] Read `/app/memory/CHANGELOG.md` last 5 entries (Iter 313-317 for context).
- [ ] Read `/app/memory/ITER_309_PART_2_SPEC.md` (the LoopStepBar spec, parallel deferred work).
- [ ] Locate `_do_execute`, `_do_ship`, `_do_verify` in `services/loop_engine.py` — line numbers likely drifted since this spec was written.
- [ ] Grep the execute-phase system prompt for elision-encouraging tokens. This is Bug 1a's root cause; do not skip.
- [ ] Write repro tests FIRST (test-first discipline). All 4 test invariants under Bug 1a + all 4 under Bug 1b + all 3 under Bug 2. Panel must fail-red before any fix code lands.
- [ ] After code changes, run full regression: Iter 312+313+315+316+317 test suites all green.
- [ ] `bug_testing_agent` verification with a synthetic elision-content payload → assert loop halts at `failed_integrity_guard`.
- [ ] Founder standalone deploy authorization required. Do NOT self-authorize.
- [ ] Update CHANGELOG honestly with live-vs-unit-only evidence flags.

Written by main-agent 2026-07-27 05:12 UTC on context-boundary handoff. Founder is Tejinder Sandhu; language preference Hinglish; standing discipline documented in system prompt and CHANGELOG.
