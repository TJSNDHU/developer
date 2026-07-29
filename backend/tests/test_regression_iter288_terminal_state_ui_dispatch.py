"""
Iter 288 — Terminal-state UI-dispatch bug fixes.

User-reported (screenshot, loop_1f8/loop_bff class): after the loop
reached a terminal FAIL state, the UI still showed the loop as alive:
  a) The LOOP · PLAN—EXECUTE—VERIFY—SCAN—SHIP stepper stayed ORANGE
     on EXECUTE instead of flipping to RED. Root cause: (i) late
     out-of-order SSE "executing" frames (e.g. per-file heartbeats
     from parallel Parliament tasks whose queue.put awaited across
     _fail's own _emit) overwrote loopPhase=error back to executing.
     (ii) errorStep was hard-coded to step 2 (EXECUTE); a ship or
     verify fail would incorrectly paint EXECUTE red anyway.
  b) The "Agent is running…" queue-status banner (Iter 284) stayed
     visible after the terminal FAIL. Root cause: `busy` was only
     cleared inside onTerminal (SSE stream close), leaving a race
     window where busy=true + terminal frame already rendered.
  c) The LoopLiveFeed heartbeat lines ("~ Execute in progress…" or
     "Still waiting on LLM response for <file> — 42s elapsed") kept
     rendering next to the FAIL message. Root cause: the ring buffer
     retained heartbeat events emitted before the FAIL frame; the
     panel never purged them on terminal.

Shared fix — three coordinated guards:
  1. `loopTerminalRef` (a synchronous useRef flag) — set the instant
     handleLoopEvent sees a terminal frame; subsequent non-terminal
     frames are DROPPED before they can mutate loopPhase / feed.
  2. Inside handleLoopEvent, when state === "failed", explicitly:
       - remember the failing phase in `loopErrorPhase` state
       - setBusy(false) and setLoopTerminal(true) immediately (no
         longer waiting for onTerminal's stream close)
  3. LoopStepBar's errorStep is now derived from `loopErrorPhase`
     via {plan:1, execute:2, verify:3, security:4, scan:4, ship:5}
     — never hard-coded to 2.
  4. LoopLiveFeed adds a useEffect on `terminal` that filters every
     heartbeat / keepalive entry out of the events buffer.

Every assertion below reproduces one of the three symptoms via
static source inspection — the pattern the rest of the /app/backend
frontend regression suite uses (see iter281/iter283/iter284/iter285
files). Runtime browser proof will follow via bug_testing_agent.
"""
from __future__ import annotations


CHAT_PANEL = "/app/frontend/src/components/ChatPanel.jsx"
STEP_BAR   = "/app/frontend/src/components/LoopStepBar.jsx"
LIVE_FEED  = "/app/frontend/src/components/LoopLiveFeed.jsx"


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── Symptom (a): stepper stays orange after FAIL ─────────────────────

def test_regression_iter288_terminal_ref_guard_present_in_handle_loop_event():
    """`handleLoopEvent` MUST short-circuit when a terminal frame has
    already been received AND the incoming frame is non-terminal. The
    ref is the only synchronous defence against a late "executing"
    frame arriving after the terminal one and flipping loopPhase back."""
    src = _read(CHAT_PANEL)
    assert "loopTerminalRef" in src, (
        "iter288: introduce `loopTerminalRef` useRef to guard against "
        "late out-of-order SSE frames"
    )
    # The guard MUST live inside handleLoopEvent, before the phase map.
    hle = src.find("function handleLoopEvent(ev)")
    assert hle > -1, "handleLoopEvent must exist"
    phase_map = src.find('if (state === "executing")', hle)
    assert phase_map > -1
    guard_snippet = src[hle:phase_map]
    assert "loopTerminalRef.current" in guard_snippet, (
        "the terminal-guard MUST run BEFORE the phase-map switch, "
        "not after — otherwise loopPhase gets clobbered."
    )
    assert "isTerminalFrame" in guard_snippet, (
        "the guard should classify terminal vs non-terminal frames "
        "and only drop the non-terminal ones."
    )


def test_regression_iter288_error_step_maps_to_actual_failed_phase():
    """LoopStepBar's `errorStep` prop was previously hard-coded to `2`
    (EXECUTE). A ship-time or verify-time failure would still paint
    EXECUTE red. The fix maps `loopErrorPhase` → the correct step id."""
    src = _read(CHAT_PANEL)
    assert "loopErrorPhase" in src, (
        "iter288: `loopErrorPhase` state must exist and be set from "
        "the failed SSE frame's `phase` field"
    )
    # The mapping must be present near the LoopStepBar render.
    assert "{plan:1, execute:2, verify:3, security:4, scan:4, ship:5}" in src, (
        "phase → step-id map must be present so ship/verify fails "
        "colour the RIGHT step red"
    )
    # And the mapping must be used inside errorStep prop of LoopStepBar.
    idx = src.find("<LoopStepBar")
    assert idx > -1
    tail = src[idx: idx + 500]
    assert "loopErrorPhase" in tail, (
        "LoopStepBar's errorStep must read loopErrorPhase, not the "
        "hard-coded 2"
    )
    # Kill the exact old smell so a future refactor cannot re-hardcode it.
    assert 'errorStep={loopPhase === "error" ? 2 : 0}' not in src, (
        "iter288: remove the hard-coded errorStep=2. Use loopErrorPhase."
    )


def test_regression_iter288_error_phase_reset_on_new_stream():
    """A fresh loop stream MUST reset the error-phase state — otherwise
    a second attempt in the same session inherits the previous run's
    red step."""
    src = _read(CHAT_PANEL)
    open_stream = src.find("function openLoopStream(lid)")
    assert open_stream > -1
    body = src[open_stream: open_stream + 2000]
    assert "setLoopErrorPhase(null)" in body, (
        "openLoopStream must reset loopErrorPhase for a clean run"
    )
    assert "loopTerminalRef.current = false" in body, (
        "openLoopStream must clear the terminal ref so the guard "
        "accepts the next run's events"
    )


# ── Symptom (b): "Agent is running…" persists after FAIL ─────────────

def test_regression_iter288_failed_frame_clears_busy_synchronously():
    """The `busy` state must be cleared inside handleLoopEvent the
    instant a terminal FAIL frame arrives — NOT wait for onTerminal
    (SSE stream close). That closes the race window that let the
    "Agent is running…" banner render alongside the FAIL message."""
    src = _read(CHAT_PANEL)
    hle = src.find("function handleLoopEvent(ev)")
    end = src.find("\n  }", hle + 20)          # end of the function block
    fn_body = src[hle:end]
    assert 'state === "failed"' in fn_body
    # The failed branch must call setBusy(false) + setLoopTerminal(true).
    assert "setBusy(false)" in fn_body, (
        "handleLoopEvent must setBusy(false) when a failed frame arrives"
    )
    assert "setLoopTerminal(true)" in fn_body, (
        "handleLoopEvent must setLoopTerminal(true) on failed so the "
        "heartbeat panel + gap-fallback stop rendering immediately"
    )


# ── Symptom (c): heartbeat lines still render after FAIL ─────────────

def test_regression_iter288_live_feed_purges_heartbeats_on_terminal():
    """Iter 344 rewrite — the Iter 309 LoopLiveFeed rewrite REMOVED
    heartbeat/keepalive rendering entirely: only `data.type ===
    "narration"` events produce rows, so there is nothing left to
    purge on terminal. The regression this locked (stale "Still
    waiting on LLM response…" rows next to a FAIL message) is now
    structurally impossible; this test locks the narration-only
    contract instead."""
    src = _read(LIVE_FEED)
    # The narration gate must exist — non-narration frames (heartbeats,
    # keepalives, state transitions) return null and never render.
    assert 'd.type !== "narration"' in src, (
        "LoopLiveFeed must gate rows on data.type === 'narration' — "
        "removing this gate lets heartbeat/keepalive frames render "
        "again (iter288 regression)."
    )
    # The old heartbeat copy must never come back.
    assert "Still waiting on LLM response" not in src, (
        "iter288: heartbeat 'Still waiting…' copy reappeared in "
        "LoopLiveFeed"
    )


# ── Shared root cause: single defensive guard ────────────────────────

def test_regression_iter288_terminal_guard_covers_all_three_symptoms():
    """All three symptoms share one root cause — late/out-of-order SSE
    frames after a terminal event. This regression asserts the guard
    is unified (single `loopTerminalRef` check) rather than patched
    per-symptom, so a future maintainer can't accidentally remove one."""
    src = _read(CHAT_PANEL)
    # The guard is exactly one ref used inside handleLoopEvent.
    assert src.count("loopTerminalRef") >= 3, (
        "loopTerminalRef must be defined, guarded on read, and set on "
        "write — at least 3 references."
    )
    # And it must be reset by openLoopStream so a second run works.
    assert "loopTerminalRef.current = false" in src
