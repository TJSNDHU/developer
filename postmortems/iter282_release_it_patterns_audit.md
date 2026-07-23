# Iter 282 — Release It! patterns audit (Bulkhead / Steady State / Governor)
Date: 2026-02-05
Regression tests:
- `test_regression_iter282_bulkhead_project_isolation`
- `test_regression_iter282_sse_stream_has_wallclock_ceiling`
+ 4 always-on fitness invariants (see `test_release_it_patterns_iter282.py`).

## What happened
Not a user-reported bug — a proactive audit against the three Release
It! patterns most likely to bite this codebase, given the loop-machinery
workload it runs.

## Root cause
Two of the three patterns had real gaps:

1. **Steady State**: none of `loop_events`, `loop_locks`,
   `loop_failures`, `loop_sessions`, `loop_verification_log`, or
   `loop_run_log` had a TTL index. Every write survived forever.
   At production traffic (multiple loops/day, 15-30 events per loop)
   the `loop_events` collection would grow unboundedly.
2. **Governor**: the SSE stream in `routers/loop.py::stream_loop`
   had a `while True` with NO wall-clock ceiling. A loop that
   stayed non-terminal (stuck in verifying/scanning) could keep the
   generator alive forever, tying up an app worker.

Bulkhead was already correct — `loop_locks` has a `unique index on
{project_id, user_id}` so user A's stuck loop cannot block user B
on the same project.

## Fix
1. **Steady State**: added 6 TTL indexes to `init_prod_collections.py`
   (retention tiered: 7d for ephemeral runtime, 30d for audit, 90d
   for analytics). Applied to the running DB out-of-band with
   `db.<coll>.create_index([(field, 1)], expireAfterSeconds=...)`.
2. **Governor**: `routers/loop.py::stream_loop::gen` now samples
   `time.monotonic()` at every iteration and breaks out with a
   synthetic terminal `aborted` frame after `_STREAM_MAX_S = 20 min`.
3. **Bulkhead**: added a regression test that spawns two users on
   the same project and asserts both can acquire locks concurrently
   — locks in the current shape, but if a future refactor widened
   the unique index to `{project_id}` alone it would trip CI.

## Why our tests missed it
- No prior audit against a checklist. The other 5 collections that
  already had TTL (`cto_notification_dismissals`, `warm_start_jobs`,
  etc.) were retrofitted one-at-a-time as bugs surfaced, so the
  6 loop-machinery ones fell through the gaps.
- The `while True` was written before Release It!-pattern awareness
  entered the codebase. No fitness invariant looked for uncapped
  loops in request-handling code.

## Prevention (what's now permanent)
- 2 regression tests + 4 fitness invariants in
  `test_release_it_patterns_iter282.py`.
- New section in `AGENTS.md` § "Release It! patterns checklist"
  (immediately above the regression-test naming index).

## MTTR
- Reported:  2026-02-05T21:00:00Z  (proactive — not a user report)
- Deployed:  2026-02-05T21:35:00Z
- Total:     ~0.58 h

## Not-follow-ups
- Not adding a Governor ceiling to every background daemon `while True`
  (db_backup, supabase_sweeper, canary, digest, etc.) — those ARE
  intended to be perpetual. Only user-facing / request-scoped loops
  need a ceiling.
- Not backfilling every non-loop collection with TTL — scope was
  the 6 loop-machinery collections named in the user's request.
