# AGENTS.md — AUREM CTO project rules for any coding agent

> This file follows the widely-adopted [AGENTS.md](https://agents.md/)
> convention. It is read by Cursor, Aider, Codex, Continue.dev,
> Claude Code, and any other agent that respects that convention.
>
> **Note about Emergent E1**: Emergent's E1 build agent does NOT
> auto-load this file into its system prompt. Persistence across E1
> sessions relies on the handoff-summary mechanism plus the
> mechanical CI gate in `.github/workflows/quality-gate.yml`.
> That gate is the enforcement layer — this file is the reference.

---

## Bug-fix discipline (always active, no exceptions)

Every bug fix in this codebase MUST ship together with a permanent
regression test that reproduces the exact broken scenario — not a
generic smoke test. Name it so its origin is traceable:

```
test_regression_iter<N>_<short_description>
```

Location: `/app/backend/tests/`. This test runs in CI on every
future deploy, forever. Six current examples live in
`/app/backend/tests/test_regression_iter279_281_bug_per_fix.py`.

---

## Touching existing untested code

Whenever an existing file/function with no test coverage is modified
— for a bug fix or a new feature — add ONE characterization test
first that locks in its current real behavior, before making the
change. Do not attempt to backfill the whole codebase at once; this
applies file-by-file, opportunistically, as files are touched.

Highest-priority files (already produced bugs this session):
- `backend/services/loop_engine.py`
- `backend/routers/loop.py`
- `frontend/src/components/ChatPanel.jsx`

---

## Verification standard

"Should work," a passing unit test with mocked dependencies, or a
demo-route screenshot are NOT sufficient proof that a
frontend/backend integration point (e.g. anything involving SSE,
loop state, or ChatPanel) actually works. Real proof means a real
browser session or a real end-to-end trigger with actual captured
output — screenshot, curl response, or console log — attached to
the completion report.

---

## Code-quality standard — senior engineer, not AI-generated feel

1. **Style consistency** — match the EXISTING file's naming
   conventions, indentation, comment density, and error-handling
   pattern exactly. Don't introduce a new pattern where one is
   already established. For new files, match the dominant style of
   2-3 similar existing files.
2. **No obvious/redundant comments** — explain WHY, never WHAT.
   Delete comments that just restate the line below them.
3. **No dead scaffolding** — no unused imports, no commented-out
   code, no leftover TODOs/placeholders in the diff.
4. **Reuse over reinvent** — search for an existing helper/utility
   before writing a new one.
5. **Right-sized, not defensive-by-default** — solve the actual
   problem asked, not every unrequested hypothetical edge case.

---

## Graceful degradation

Every user-facing feature needs a fail-safe fallback:

- Backend timeout → clear "something went wrong, try again"
  message, never a silent hang or blank screen.
- Failed API call → only that section shows an error, page doesn't
  crash.
- A feature found broken in production → can be disabled instantly
  via a feature flag, without a full redeploy, while it's fixed.

Concrete example (this session): `LoopLiveFeed.jsx` previously
returned `null` when it had no SSE events yet. This looked to the
user like the feature was broken. Now it renders a
`[data-testid=loop-live-feed-placeholder]` pending state — the
panel is always visible once a `loopId` exists.

---

## Fitness-function invariants (CI-enforced)

The following are always-on assertions in
`/app/backend/tests/test_invariants_continuous_quality.py`. They
must never fail on `main`:

1. `chat-input` is never `disabled={busy…}` or `disabled={loop…}`.
2. `cancel_loop` releases lock + sets `state=aborted` in < 2s.
3. Every `self.state = LoopState.<X>` in `loop_engine.py` is
   followed by a co-located `self._emit(...)` call.
4. `LoopLiveFeed` never returns `null` when a `loopId` is set.

---

## Error Budget Policy (Google SRE) + DORA Four Keys

Reliability is a feature. We measure and gate on the four keys
published by Google's [DORA](https://dora.dev/) research program,
using their exact terminology so we can benchmark against the
public Elite/High/Medium/Low tiers instead of inventing our own
labels.

| DORA metric               | Where it lives                         | Threshold (this repo)     |
|---------------------------|----------------------------------------|---------------------------|
| Deployment Frequency      | `loop_outcomes` `ship_at` timestamps   | informational (no gate)   |
| Lead Time for Changes     | commit → `ship_at` delta               | informational (no gate)   |
| **Change Failure Rate**   | `loop_outcomes.revert_within_24h`      | **≤ 10%** (14-day window) |
| **MTTR** (Mean Time To Restore) | `/app/memory/mttr_log.json`      | **≤ 24 h**                |

**Naming rule**: anywhere the code or dashboard currently says
"revert-rate", we now spell it **Change Failure Rate** in labels,
comments, and docs. The underlying computation is unchanged — this
is purely a naming alignment with DORA so the number is
benchmarkable. Grep for `revert-rate` / `revert_rate` on next touch
of that surface and rename in place (opportunistic — not a sprint).

### Error Budget Policy — what happens when Change Failure Rate > 10%

1. New feature work FREEZES on `main` immediately.
2. Only reliability fixes may merge — labelled `[reliability-fix]`.
   The CI quality-gate accepts these WITHOUT the normal test-file
   requirement (they may just be revert-shaped) but each MUST link
   a postmortem doc (see next section) in the PR description.
3. Feature-work freeze lifts automatically once the trailing
   14-day rate returns below 10%. No committee vote.

**Where the number lives**: `services/loop_outcomes.py::rolling_
change_failure_rate()` — **NOT YET IMPLEMENTED as of iter 282.**
The 10% budget is declared here, but no code computes it, no
dashboard reads it, and no gate enforces it. Do not present CFR
numbers to users, in dashboards, or in status reports until this
function exists and has been validated against real revert data.
Placeholder constant `CHANGE_FAILURE_BUDGET = 0.10` will live in
that module when it is built. Founder dashboard will surface it as
a single green/red indicator next to the daily brief AT THAT POINT
— not before.

### MTTR tracking (starts now)

Every bug that reaches production and gets fixed writes ONE row to
`/app/memory/mttr_log.json`:

```json
{
  "iter": 279,
  "slug": "cancel-race-condition",
  "reported_at":  "2026-02-05T18:12:00Z",
  "deployed_at":  "2026-02-05T20:47:00Z",
  "mttr_hours":   2.58,
  "postmortem":   "postmortems/iter279_cancel_race.md"
}
```

**Retroactive backfill**: only for iters where both timestamps are
trivially recoverable from `CHANGELOG.md` (the "date-stamped
chunk" header) + the actual deploy history. Skip anything that
would need archaeology — going forward is what matters.

---

## Blameless Postmortem template

For major bugs — anything in the "ghost-task / cancel-race /
SSE-wiring / silent-swallow" class — the regression test alone
is not enough. Write a short human-readable doc alongside it.

Location: `/app/postmortems/iter<N>_<slug>.md`.

Template (keep it short — one page max):

```
# Iter <N> — <one-line title>
Date: YYYY-MM-DD
Regression test: test_regression_iter<N>_<slug>

## What happened
2-4 sentences. What did the user experience? What did the system
actually do?

## Root cause
The ONE technical reason. If there are two, pick the deeper one —
the other is a symptom.

## Fix
One paragraph. What we changed and why THAT change is the correct
lever. Reference the code location(s).

## Why our tests missed it
Honest answer. Usually one of:
   - No integration-level test covered this seam
   - The mock in the unit test hid the real failure mode
   - The bug is a race — our test suite has no time dimension yet

## Prevention (what's now permanent)
- Regression test: <path>
- Fitness invariant (if any): <path>
- Rule added to AGENTS.md (if any): <section anchor>

## MTTR
- Reported:  YYYY-MM-DDTHH:MM:SSZ
- Deployed:  YYYY-MM-DDTHH:MM:SSZ
- Total:     <hours>

## Not-follow-ups
Explicitly list what we're NOT doing, so future readers don't ask.
E.g. "not refactoring loop_engine.py in this pass — deferred to
iter N+K per user instruction."
```

Blameless means: no names, no "should have caught this earlier",
no finger-pointing at agents or humans. The system failed; that
is the object of study.

---

## Release It! patterns checklist

Michael Nygard's [Release It!](https://pragprog.com/titles/mnee2/release-it-second-edition/)
lists the failure modes distributed systems keep re-inventing.
Three that apply directly to this codebase — audited in iter 282,
locked with permanent tests. Any new subsystem must be checked
against these three before shipping:

1. **Bulkhead** — resources are partitioned so one bad tenant can't
   starve the others. Concretely for us: the `loop_locks` unique
   index MUST be composite `{project_id, user_id}`, never just
   `{project_id}`. User A's stuck loop must not block user B on
   the same project.  See `test_invariant_bulkhead_unique_index_declared`.
2. **Steady State** — every collection that grows with traffic
   MUST declare a TTL. If you add a new collection to
   `init_prod_collections.py`, it needs an `expireAfterSeconds`
   line UNLESS the collection is user-owned data (accounts,
   projects, chat sessions) — those are size-of-user-base, not
   size-of-time. See `test_invariant_loop_collections_have_ttl_indexes`.
3. **Governor** — every user-facing / request-scoped `while True`
   loop MUST have a wall-clock ceiling. Background daemons
   (db_backup, canary, digest) are intentionally perpetual and
   exempt; SSE generators, retry loops, and long-poll handlers
   are NOT. See `test_regression_iter282_sse_stream_has_wallclock_ceiling`.

Circuit Breaker and Timeout patterns are already present via
`services/loop_safety.py::is_loop_circuit_open` and
`shared/resilience/circuit_breaker_service.py` — not audited in
this iter because they were already in place, but same discipline
applies: any new external call goes through them.

---

## Regression-test naming index (grow this list)

- `test_regression_iter277_ghost_task_terminal_frame`
- `test_regression_iter278_heartbeat_frames_every_6s`
- `test_regression_iter279_cancel_race_condition`
- `test_regression_iter280_chat_input_enabled_during_loop`
- `test_regression_iter280_chat_history_persists_on_reload`
- `test_regression_iter281_plan_approval_reachable_from_any_prior_state`
- `test_regression_iter281_loop_live_feed_pending_placeholder`
- `test_regression_iter281_intent_tier_indicator_no_null_return`
- `test_regression_iter282_bulkhead_project_isolation`
- `test_regression_iter282_sse_stream_has_wallclock_ceiling`
- `test_regression_iter282_ttl_bootstrap_resolves_index_conflict`
- `test_regression_iter283_chatpanel_stop_calls_cancel_loop`
- `test_regression_iter283_backend_cancels_paused_for_user_loop`
- `test_regression_iter284_chat_queue_send_button_renders_during_busy`
- `test_regression_iter284_window_confirm_removed`
- `test_regression_iter284_queued_chip_and_agent_running_present`
- `test_regression_iter285_chat_inline_card_class_declared_in_css`
- `test_regression_iter285_container_queries_include_chat_inline_card`
- `test_regression_iter285_plan_approval_and_live_feed_use_wrapper`
- `test_regression_iter286_write_repo_file_blocks_test_files`
- `test_regression_iter286_write_repo_file_allows_test_files_with_override`
- `test_regression_iter286_write_repo_file_allows_normal_paths`
- `test_regression_iter286_ship_code_has_test_file_gate_in_source`
- `test_regression_iter286_ship_code_override_not_llm_grantable`
