# Iter 277 — Ghost task: cancelled loop still rendered "executing"
Date: 2026-07-23
Regression test: `test_regression_iter277_ghost_task_terminal_frame`

## What happened
A user's loop (`loop_c03195e76ca04e`) was killed by a worker
restart. The DB row stayed at `state=executing` with `updated_at`
frozen for 20+ minutes. The user's chat kept rendering the stale
"executing" state; worse, `/loop/active?project_id=X` kept
returning the ghost, so any NEW chat opened in the same project
auto-rehydrated the dead loop.

## Root cause
The `/cancel` fallback branch — the path taken when there's no
live engine to cancel — updated `loop_sessions.state` but never
emitted a terminal frame. The frontend's SSE consumer only closes
the stream on `state ∈ {completed, failed, aborted}`; without a
terminal frame it kept the "executing" UI up until the tab was
manually reloaded.

## Fix
`routers/loop.py::cancel_loop` fallback branch now writes both
`loop_sessions.state="aborted"` (with fresh `updated_at` and a
populated `last_event`) AND inserts a row in `loop_events` tagged
`data.origin="cancel_fallback"`. The response body includes
`terminal_event_written: true` so the client can optimistically
flip the UI without waiting the ~2s SSE poll cycle.

## Why our tests missed it
- Existing tests exercised the HAPPY path (live engine + graceful
  cancel), not the fallback branch that only fires after a worker
  restart drops the engine reference from `_LIVE`.
- The SSE stream itself was mocked in unit tests — real backend
  behaviour of "server never emits terminal frame" was invisible.

## Prevention (what's now permanent)
- Regression test: `backend/tests/test_regression_iter279_281_bug_per_fix.py::test_regression_iter277_ghost_task_terminal_frame`
- Fitness invariant: `test_invariant_every_sse_event_reaches_frontend_playwright` — every `self.state = LoopState.<X>` in `loop_engine.py` must have a co-located `_emit()` within 40 lines.
- Rule added to AGENTS.md: "Graceful degradation" section (every user-facing feature needs a fail-safe fallback).

## MTTR
- Reported:  2026-07-23T00:26:42Z
- Deployed:  2026-07-23T02:11:00Z
- Total:     ~1.7 h

## Not-follow-ups
- Not refactoring `loop_engine.py` to reduce the size of the ghost-recovery branch — deferred to a dedicated refactor iter.
- Not adding a proactive ghost-sweeper cron — the on-cancel fallback is enough for now; sweeper stays a "future/backlog" item.
