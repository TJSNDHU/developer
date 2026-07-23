# Iter 281 — LoopLiveFeed silently invisible + runLoopPlan swallowed loop prompts
Date: 2026-02-05
Regression tests:
- `test_regression_iter281_plan_approval_reachable_from_any_prior_state`
- `test_regression_iter281_loop_live_feed_pending_placeholder`
- `test_regression_iter281_intent_tier_indicator_no_null_return`

## What happened
Two related symptoms reported on production:
1. `LoopLiveFeed` "just doesn't show up" during a real loop run.
2. Submitting a LOOP prompt right after a Mode-D auto-diagnosis
   card had rendered did nothing — the prompt sat un-sent in the
   composer.

## Root cause
Two independent gaps in graceful degradation, exposed together:
1. **`LoopLiveFeed.jsx:116` returned `null` when
   `events.length === 0`.** `openLoopStream()` only fires AFTER
   the user clicks Plan Approval, so during the entire
   plan-approval-pending window (which can be 15-60s while the
   Council writes the plan) the panel was absent from the DOM
   entirely, giving the impression the feature was broken.
2. **`ChatPanel.jsx::runLoopPlan` early-returned on `busy=true`.**
   Meanwhile `send()` had been specifically patched (Iter 280) to
   whitelist LOOP-mode busy re-entry so the Iter 279 queue-next
   flow could trigger. The `runLoopPlan` guard silently undid
   that patch — the 409 dialog was unreachable and the prompt
   vanished.

## Fix
1. `LoopLiveFeed.jsx`: renders a
   `[data-testid="loop-live-feed-placeholder"]` block whenever
   `loopId` is set but no events have arrived. The `data-testid=
   "loop-live-feed"` node is now present in the DOM the instant
   a loop_id exists.
2. `ChatPanel.jsx::runLoopPlan`: removed the `busy` early-return,
   kept only the `!sessionId` guard. The 409 path in `catch{}` is
   idempotent — the queue/cancel-restart dialog now triggers
   reliably.
3. Audit follow-up: `IntentTierIndicator.jsx` had the SAME
   null-on-empty pattern. Fixed identically (default to `casual`
   tier, mark with `data-pending="true"`).

## Why our tests missed it
- Playwright E2E tests were happy-path — they clicked Plan
  Approval before checking for LoopLiveFeed, hiding the "invisible
  during pending" window entirely.
- No fitness invariant asserted the "component must exist in DOM
  once its parent state is set" property. All UI tests were
  positive assertions ("this appears when X"), never negative
  ones ("this must never DISAPPEAR when X is still set").

## Prevention (what's now permanent)
- 3 regression tests locking the source patterns.
- **New fitness invariants**: `test_invariant_loop_live_feed_never_returns_null` and `test_invariant_intent_tier_indicator_never_returns_null` — both source-level, guarding the graceful-degradation rule.
- Rule added to `AGENTS.md` § "Graceful degradation": components with a parent-controlled `visible` prop MAY return null; components whose visibility is derived from their OWN async state MUST render a `[data-pending]` placeholder instead.

## MTTR
- Reported:  2026-02-05T18:12:00Z
- Deployed:  2026-02-05T20:47:00Z
- Total:     ~2.6 h

## Not-follow-ups
- Not auditing every other component for the same pattern in this pass — scope was limited to the two the user named (SelfHealIndicator + IntentTierIndicator). SelfHealIndicator uses a controlled `visible` prop and was found correct. Broader audit deferred.
- Not starting Phase 3 (Continuous Codebase Watcher) — explicitly deferred to a fresh session per user instruction.
