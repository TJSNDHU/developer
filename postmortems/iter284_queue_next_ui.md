# Iter 284 — Queue-next UX: hidden send button + narrow window.confirm
Date: 2026-02-05
Regression tests:
- `test_regression_iter284_chat_queue_send_button_renders_during_busy`
- `test_regression_iter284_window_confirm_removed`
- `test_regression_iter284_queued_chip_and_agent_running_present`

## What happened
User's own screenshot: an active loop is `thinking · 51.2s`, user
typed a follow-up prompt "and when", but the composer only showed
the `chat-stop` button — no send affordance. The Iter 279 queue-
next flow was designed to activate on send during busy, but the
click entry point was hidden. User also called out that the
queue-confirm popup would be OS-narrow (`window.confirm`), visually
detached from the composer.

## Root cause
Two problems in ChatPanel.jsx:
1. **Send button hidden while busy**: the composer's send/stop
   render was a straight ternary — `{busy ? <chat-stop> :
   <chat-send>}`. When busy=true, chat-send vanished from the DOM.
   Enter key still worked (form onSubmit), but the affordance was
   undiscoverable.
2. **`window.confirm()` for the queue prompt**: the 409-queue
   handler used an OS-native confirm dialog. Not styled, narrow,
   detached from the app's aesthetic. Also blocked the JS main
   thread until dismissed.

## Fix
1. **Show `chat-queue-send` alongside `chat-stop` when busy AND
   execMode=LOOP AND input has text AND session is ready.** Clicking
   it fires the same `send()` handler, which now reaches the 409
   path reliably (Iter 281's runLoopPlan fix + Iter 283's stop path
   fix cleared the way).
2. **Silent auto-queue on 409** — no confirm dialog at all. Instead,
   a caption row above the composer surfaces the state:
   - `[data-testid="queued-chip"]` — "▸ N queued"
   - `[data-testid="agent-status-bar"]` — "Agent is running…" with
     a pulsing orange dot
   - Both live in an amber-outlined row that visually pairs with the
     composer below (the composer's top-corner radius flattens to
     make it look like one container).
3. The queue counter (`queuedCount`) increments on 409 → decrements
   when the queued run actually fires. Purely additive React state.

## Why our tests missed it
- Iter 279 added the backend 409 → queue path. All regression tests
  focused on the backend contract (409 returns correct fields).
  None asserted the UI actually surfaced the queue affordance.
- The `window.confirm` was inside a browser-only code path — no
  unit test exercised the JSX branch. Only real-user screenshots
  could have caught the affordance-invisible failure mode.

## Prevention (what's now permanent)
- 3 source-level tests:
  - `chat-queue-send` MUST render in the busy branch.
  - `window.confirm(` MUST NOT exist in the queue-next handler.
  - `agent-status-bar` + `queued-chip` testids MUST exist.
- Rule added implicitly to AGENTS.md § "Graceful degradation":
  "any feature reachable only via keyboard shortcut MUST also
  render a visible affordance". Codifies the "undiscoverable
  entry point" class of bug so it's on the future-author radar.

## MTTR
- Reported:  2026-02-05T23:15:00Z  (user screenshot)
- Deployed:  2026-02-05T23:55:00Z
- Total:     ~0.67 h

## Not-follow-ups
- Not showing a dropdown "peek queued messages" list in this pass —
  a simple counter is enough for now. Grow when we see >1-2
  queued as a common pattern.
- Not moving the caption bar layout into a separate component —
  ~40 LOC inlined into ChatPanel is under the extraction threshold.
