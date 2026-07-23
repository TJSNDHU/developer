# Iter 283 — chat-stop didn't cancel `paused_for_user` loops
Date: 2026-02-05
Regression tests:
- `test_regression_iter283_chatpanel_stop_calls_cancel_loop`
- `test_regression_iter283_backend_cancels_paused_for_user_loop`

## What happened
During the Iter 282 full-QA E2E on production, checkpoint
`Iter279:backend-cancelled-within-2s` failed. A loop had reached
the SHIP-approval gate (`state="paused_for_user"`, `phase="ship"`).
The user clicked Stop; the frontend's `stop()` handler ran; the
`/loop/active` API still returned the loop 4 seconds later with
the same paused state.

## Root cause
`ChatPanel.jsx::stop()` aborted the local `AbortController`s for
the SSE and chat streams but never called `cancelLoop(loopId)`.

For an ACTIVELY streaming loop this happened to be enough — when
the client SSE disconnects, the server-side generator's `finally`
detects it and cleans up. But a loop in `paused_for_user` state
had no active stream to disconnect; the engine was idle in
`_LIVE`, waiting on user input. Nothing on the server ever heard
about the Stop click.

## Fix
`stop()` now unconditionally calls `cancelLoop(loopId)` if a
`loopId` is set. The call is guarded with `.catch()` so a stale-
id or 404 doesn't throw into the sync `stop` path. Also added
`loopId` to the `useCallback` deps so the closure sees the
current id.

Backend `cancel_loop` already handled all states correctly (its
Iter 212m-131 code path flips state → ABORTED regardless of
current state and releases the lock). Zero server changes needed.

## Why our tests missed it
- The Iter 279 regression test covered `active → aborted` — it
  never exercised `paused_for_user → aborted`.
- No fitness invariant asserted that `stop()` calls the backend
  cancel endpoint. All prior tests looked at UI behavior (loops
  cancelled from an actively-streaming state, where the abort
  happened to work by accident of the SSE-detects-disconnect path).
- Full E2E on prod is what surfaced this; a source-level or
  isolated unit test wouldn't have caught the missing call
  because the code compiled cleanly.

## Prevention (what's now permanent)
- Regression test 1 (source-level): `stop()` MUST contain
  `cancelLoop(loopId)` guarded by `if (loopId)`, with `loopId` in
  the `useCallback` deps.
- Regression test 2 (integration): backend `paused_for_user →
  aborted` transition writes state + releases lock + emits
  terminal event, matching the Iter 277 ghost-task fix contract.

## MTTR
- Reported:  2026-02-05T22:20:00Z  (surfaced by our own E2E, not a user report)
- Deployed:  2026-02-05T23:00:00Z
- Total:     ~0.67 h

## Not-follow-ups
- Not adding a proactive "detect abandoned paused_for_user loops
  and auto-cancel after N hours" cron — separate concern, deferred.
- Not changing the frontend to hide the chat-stop button while
  in a paused state — the current behavior (Stop always visible)
  is safer, and now the button actually works from that state.
