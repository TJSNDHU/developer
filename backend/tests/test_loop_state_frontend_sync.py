"""
test_loop_state_frontend_sync.py — Iter 308 v2

Guarantees the backend LoopState enum stays in sync with the frontend
`PHASE_TO_STEP` mapping in LoopStepBar.jsx.

The bug this catches: iter 308 root cause included an INCOMPLETE
frontend mapping. Backend added `self_healing` and `paused_for_user`
states over time; frontend was never updated. Unmapped states fell
through to step=0 and the WHOLE progress bar rendered grey — user
perceived this as "stuck/broken" (see loop_643 report, 2.5 hrs).

If a future engineer adds a new state to LoopState WITHOUT adding it
to PHASE_TO_STEP, this test fires immediately in CI — no waiting for
a user to file the same bug.

Companion test — the ChatPanel.jsx `handleLoopEvent` switch also
carries an exhaustive state → loopPhase mapping. We assert the same
enum coverage there.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.loop_engine import LoopState


def _backend_state_values() -> set[str]:
    """Set of all LoopState.value strings the engine can emit."""
    return {s.value for s in LoopState}


def _read_frontend(path: str) -> str:
    fp = Path(__file__).resolve().parents[2] / "frontend" / path
    assert fp.is_file(), f"missing frontend file: {fp}"
    return fp.read_text()


def test_LoopStepBar_covers_every_backend_state():
    """Every backend LoopState.value MUST appear as a key in
    frontend/src/components/LoopStepBar.jsx's PHASE_TO_STEP object.

    Extra keys are allowed (frontend-only synonyms like `done`,
    `error`, `security`) but every backend value must be present."""
    src = _read_frontend("src/components/LoopStepBar.jsx")
    # Extract PHASE_TO_STEP body: everything between `PHASE_TO_STEP = {`
    # and the closing `};`.
    m = re.search(r"PHASE_TO_STEP\s*=\s*\{(.*?)\};", src, re.S)
    assert m, "PHASE_TO_STEP object not found in LoopStepBar.jsx"
    body = m.group(1)
    # Keys are bareword or quoted identifiers anywhere in the body —
    # Iter 344: the old `^`-anchored regex only matched the FIRST key
    # per line, falsely reporting multi-key lines (`plan_pending: 1,
    # planning: 1, …`) as missing. Runtime twin:
    # frontend/src/components/__tests__/LoopStepBar.iter344_phase_map_runtime.test.jsx
    # imports the executed map and asserts the same coverage.
    keys = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", body))
    backend = _backend_state_values()
    missing = backend - keys
    assert not missing, (
        "LoopStepBar.jsx PHASE_TO_STEP is missing frontend mappings "
        f"for backend LoopState value(s): {sorted(missing)}. "
        "This class of drift produced the 2.5-hr stuck-execute prod "
        "bug (loop_643) — unmapped states rendered ALL step icons "
        "grey. Add the missing key(s) with the correct step number, "
        "then re-run this test."
    )


def test_ChatPanel_handleLoopEvent_covers_every_backend_state():
    """The ChatPanel switch that maps `ev.state` → `loopPhase` must
    include a branch for every backend LoopState.value, so the step
    bar always gets a fresh value on state transitions. Prior gap:
    self_healing / paused_for_user / expired had no branch, so
    loopPhase stayed frozen at the last matched value (usually
    'executing') — user saw the orange EXECUTE ring spinning forever
    even though the engine had actually moved on."""
    src = _read_frontend("src/components/ChatPanel.jsx")
    # Find the handleLoopEvent function body (~150 lines).
    m = re.search(r"function\s+handleLoopEvent\s*\([^)]*\)\s*\{(.*?)\n  \}",
                  src, re.S)
    assert m, "handleLoopEvent function not found in ChatPanel.jsx"
    body = m.group(1)
    # Collect every state literal that appears in a `state === "..."`
    # comparison.
    handled = set(re.findall(r'state\s*===\s*"([^"]+)"', body))
    backend = _backend_state_values()
    missing = backend - handled
    assert not missing, (
        "ChatPanel.jsx::handleLoopEvent is missing a `state === \"X\"` "
        f"branch for backend LoopState value(s): {sorted(missing)}. "
        "Every state emitted by loop_engine._emit() must produce a "
        "corresponding setLoopPhase() call, otherwise the step bar "
        "goes stale during that state's window (root cause of iter "
        "308 loop_643 stuck-execute UX bug). Add the missing branch."
    )


def test_LoopLiveFeed_placeholder_is_dynamic():
    """The LoopLiveFeed placeholder text must switch on the `phase`
    prop, not be a hardcoded literal that lies during execute/verify/
    scan/ship. Verified by asserting that the placeholder branch
    references the `phase` variable at least once."""
    src = _read_frontend("src/components/LoopLiveFeed.jsx")
    # Iter 344 — Iter 309 rewrite: the placeholder branch is now the
    # `emptyLine` IIFE (rendered when !hasLines), still phase-aware.
    m = re.search(
        r"const emptyLine = \(\(\) => \{(.*?)\}\)\(\);",
        src, re.S,
    )
    assert m, "emptyLine placeholder block not found in LoopLiveFeed.jsx"
    branch = m.group(1)
    assert "phase" in branch, (
        "LoopLiveFeed placeholder branch must switch on the `phase` "
        "prop (iter 308 root cause: the hardcoded 'Waiting for plan "
        "approval / opening event stream…' literal lied for 2.5 hrs "
        "while the user's loop_643 was actually executing)."
    )
    # And assert that at least three distinct phase-aware branches
    # exist so we know it's not just a decorative reference.
    phase_checks = re.findall(r'p\s*===\s*"([^"]+)"', branch)
    assert len(set(phase_checks)) >= 3, (
        "LoopLiveFeed placeholder should have branches for at least "
        f"3 distinct phases, got: {phase_checks}"
    )


def test_stale_after_s_startup_invariant_holds():
    """The `assert` in loop_engine.py that guarantees STALE_AFTER_S >
    max(PHASE_TIMEOUTS_S) must succeed at import time — this test
    just confirms it fires (import + verify)."""
    from services.loop_engine import STALE_AFTER_S, PHASE_TIMEOUTS_S
    assert STALE_AFTER_S > max(PHASE_TIMEOUTS_S.values()), (
        "STALE_AFTER_S startup invariant violated — the reaper can "
        "kill a legitimately-progressing phase (iter 308 root cause)."
    )
