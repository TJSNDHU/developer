# Iter 279 — Cancel race: immediate re-start hit "loop_already_running"
Date: 2026-02-04
Regression test: `test_regression_iter279_cancel_race_condition`

## What happened
A user cancelled a running loop, then within a second submitted a
new prompt. The new `/loop/start` failed with 409
`loop_already_running` because the `loop_locks` row from the
cancelled loop was still in the DB. Repeated retries eventually
worked — but only after 3-5 seconds of confusing silence.

## Root cause
`engine.cancel()` did release the lock, but the pipeline task's
own `finally` block (unwinding through `CancelledError`) could
re-persist an interim state or re-acquire the lock in a narrow
window right after the HTTP response for `/cancel` returned. So
the API said "done", but the DB said "still running" for another
1-2 seconds.

## Fix
Iter 279 belt-and-suspenders: `routers/loop.py::cancel_loop`
performs a SECOND `loop_sessions.update_one(state="aborted")` AND
`loop_locks.delete_many({...})` AFTER `engine.cancel()` returns.
The double-write is idempotent — it just guarantees the terminal
state is durable before the HTTP response is sent to the client.
Frontend `runLoopPlan` also gained the Iter 279 queue-next dialog:
on 409 it offers Queue vs Cancel-restart instead of a hard error.

## Why our tests missed it
- The `_LIVE` engine dict + `loop_locks` collection had no
  integration test exercising the "cancel + immediately start
  again" transition.
- The race window was <2s wall-clock — no fitness invariant
  measured time-to-consistency.

## Prevention (what's now permanent)
- Regression test: `test_regression_iter279_cancel_race_condition` — acquires a lock, releases it, then re-acquires with the SAME `(project, user)` and asserts wall-clock elapsed < 2 s.
- Fitness invariant: `test_invariant_cancel_within_2s_state_aborted_lock_released` — the runtime version, hitting real Mongo.
- The Iter 279 belt-and-suspenders pattern is now the documented approach for any distributed-state cancel: "the API response must not return until the DB state is consistent with the response."

## MTTR
- Reported:  2026-02-04T14:20:00Z
- Deployed:  2026-02-04T17:35:00Z
- Total:     ~3.25 h

## Not-follow-ups
- Not migrating `loop_locks` to a proper distributed lock (Redis/ZooKeeper). Mongo TTL indexes + the double-write pattern are sufficient at current scale.
