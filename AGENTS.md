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

## Regression-test naming index (grow this list)

- `test_regression_iter277_ghost_task_terminal_frame`
- `test_regression_iter278_heartbeat_frames_every_6s`
- `test_regression_iter279_cancel_race_condition`
- `test_regression_iter280_chat_input_enabled_during_loop`
- `test_regression_iter280_chat_history_persists_on_reload`
- `test_regression_iter281_plan_approval_reachable_from_any_prior_state`
- `test_regression_iter281_loop_live_feed_pending_placeholder`
