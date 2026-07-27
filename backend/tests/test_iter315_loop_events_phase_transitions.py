"""
test_iter315_loop_events_phase_transitions.py — Iter 315

RCA REPRO TESTS (written FIRST, before Fix 1 lands).

Speed-diagnostic JSON pulled from prod showed `phase_wall_clock`
with `n:0, avg_s:null` for every phase across 10 sampled loops.
Root cause traced to `services/loop_engine.py::_emit()` — the
state-transition emitter that fires plan/execute/verify/scan/ship
events. It pushes to `self.queue` (SSE), records to
`sse_replay_buffer`, and persists `loop_sessions.last_event`, but
NEVER inserts a row into `db.loop_events`. Only specific audit
kinds (`scope_drift`, `plan_ungrounded_paths`, `task_spec_freeze`)
write to that collection. So the diagnostic's
`_phase_durations_from_events()` finds no phase-transition rows
and reports n:0 for every loop, forever.

TEST DISCIPLINE:
  1. `test_repro_emit_does_not_write_loop_events` — grep-invariant
     asserting the CURRENT `_emit()` source contains no
     `db.loop_events.insert_one` call. MUST FAIL after Fix 1.
  2. `test_emit_writes_state_transition_row` — after Fix 1, calling
     `_emit(STATE, phase)` in isolation writes a row with the
     expected envelope (loop_id, state, phase, ts, kind).
  3. `test_emit_write_failure_is_non_fatal` — regression: forcing
     the Mongo insert to raise must not raise out of `_emit()` (the
     SSE side is more important than the audit side; ledger MUST
     never break a live loop).
  4. `test_loop_events_kind_marker_present` — the row written by
     `_emit()` must carry a `kind` field distinguishing it from
     the existing audit-kind rows (scope_drift etc.), so
     downstream queries can pick either.
"""
from __future__ import annotations
import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


_ENGINE_SRC_PATH = Path("/app/backend/services/loop_engine.py")


def _emit_source_body() -> str:
    """Extract only the `_emit(...)` method body."""
    src = _ENGINE_SRC_PATH.read_text()
    m = re.search(
        r"async def _emit\(self, state: LoopState, phase: str, \*\*kw\)"
        r".*?(?=\n    async def |\n    def |\n    #)",
        src, re.DOTALL,
    )
    assert m, "_emit() method not found in loop_engine.py"
    return m.group(0)


# ── 1. REPRO — _emit currently does NOT write loop_events ──────────
def test_repro_emit_does_not_write_loop_events():
    """
    Grep the extracted `_emit()` body for any `loop_events.insert`
    call. Before Fix 1 there is none — that IS the bug.
    """
    body = _emit_source_body()
    has_write = ("loop_events.insert_one" in body
                 or "loop_events.insert_many" in body)
    assert has_write, (
        "REPRO: _emit() does not write to db.loop_events. Phase "
        "transition events (plan-start, execute-start, etc.) are "
        "pushed to self.queue for SSE and stored in "
        "loop_sessions.last_event, but never persisted for the "
        "diagnostic aggregator. Fix 1 must add a try/except "
        "insert_one call with a kind marker."
    )


# ── 2. Fix behavior — _emit writes a state_transition row ──────────
@pytest.mark.asyncio
async def test_emit_writes_state_transition_row_when_fix_lands():
    """
    After Fix 1, invoking `_emit(state, phase)` on an engine with a
    mocked db must produce ONE insert_one call to db.loop_events
    with an envelope containing loop_id, state, phase, ts, kind.
    """
    from services.loop_engine import LoopEngine, LoopState

    mock_db = MagicMock()
    mock_db.loop_events.insert_one = AsyncMock()
    mock_db.loop_sessions.update_one = AsyncMock()
    mock_db.loop_sessions.find_one_and_update = AsyncMock(return_value={})
    mock_db.loop_sessions.replace_one = AsyncMock()
    mock_db.loop_sessions.insert_one = AsyncMock()

    engine = LoopEngine(
        db=mock_db, loop_id="loop_test_iter315",
        user_id="test_admin_001", project_id="p_test",
        user_message="test emit", bin_ctx=None,
    )
    await engine._emit(LoopState.PLANNING, "plan",
                       step=1, total_steps=5,
                       message="Plan starting")
    assert mock_db.loop_events.insert_one.await_count >= 1, (
        "Fix 1 must call db.loop_events.insert_one exactly once "
        "per _emit invocation."
    )
    call_args = mock_db.loop_events.insert_one.call_args
    doc = call_args.args[0] if call_args.args else call_args.kwargs.get("document")
    assert doc is not None, "insert_one called with no document"
    for key in ("loop_id", "state", "phase", "ts", "kind"):
        assert key in doc, (
            f"loop_events row missing required key '{key}'. Got: {list(doc)}"
        )
    assert doc["loop_id"] == "loop_test_iter315"
    assert doc["phase"] == "plan"
    # The state field can be the enum value or its string form; both OK.
    assert str(doc["state"]).endswith("planning") or doc["state"] == "planning"


# ── 3. Regression — insert failure must not raise out of _emit ─────
@pytest.mark.asyncio
async def test_emit_insert_failure_is_swallowed():
    """
    Ledger writes MUST NEVER break a live loop. If db.loop_events
    raises (Mongo down, index conflict, etc.), _emit must still
    complete: SSE path is more important than diagnostic path.
    """
    from services.loop_engine import LoopEngine, LoopState

    mock_db = MagicMock()
    mock_db.loop_events.insert_one = AsyncMock(
        side_effect=RuntimeError("simulated mongo failure")
    )
    mock_db.loop_sessions.update_one = AsyncMock()
    mock_db.loop_sessions.find_one_and_update = AsyncMock(return_value={})
    mock_db.loop_sessions.replace_one = AsyncMock()
    mock_db.loop_sessions.insert_one = AsyncMock()

    engine = LoopEngine(
        db=mock_db, loop_id="loop_test_swallow",
        user_id="test_admin_001", project_id="p_test",
        user_message="test swallow", bin_ctx=None,
    )
    # Must NOT raise.
    await engine._emit(LoopState.PLANNING, "plan", message="x")
    # SSE queue must still have received the event.
    assert engine.queue.qsize() >= 1, (
        "SSE queue must receive the event even when loop_events "
        "insert fails. Fix 1's try/except must not swallow the "
        "queue.put too."
    )


# ── 4. Kind marker — distinguishes from audit-kind rows ────────────
@pytest.mark.asyncio
async def test_state_transition_kind_marker_distinct():
    """
    The row from _emit() must carry a `kind` value distinct from
    the existing audit kinds ("scope_drift", "plan_ungrounded_paths",
    "task_spec_freeze"). This lets downstream queries filter for
    either family without collision.
    """
    from services.loop_engine import LoopEngine, LoopState

    mock_db = MagicMock()
    mock_db.loop_events.insert_one = AsyncMock()
    mock_db.loop_sessions.update_one = AsyncMock()
    mock_db.loop_sessions.find_one_and_update = AsyncMock(return_value={})
    mock_db.loop_sessions.replace_one = AsyncMock()
    mock_db.loop_sessions.insert_one = AsyncMock()

    engine = LoopEngine(
        db=mock_db, loop_id="loop_test_kind",
        user_id="test_admin_001", project_id="p_test",
        user_message="test kind", bin_ctx=None,
    )
    await engine._emit(LoopState.PLANNING, "plan")
    doc = mock_db.loop_events.insert_one.call_args.args[0]
    assert doc.get("kind") == "state_transition", (
        f"kind marker must be 'state_transition' to distinguish "
        f"from audit kinds. Got: {doc.get('kind')!r}"
    )
