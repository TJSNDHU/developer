"""Iter 332 — SHIP-gate P0 fix.

Founder prod smoke test found: (a) the human-review ship gate had no
"Approve & Ship" button, and (b) "Skip this step" re-ran the pipeline
from EXECUTE, re-hit the same gate, and cycled forever.

Behavioral tests: `skip_at_ship()` must terminate the loop gracefully
(ABORTED + terminal_reason=SKIPPED_AT_SHIP), and the Iter 332 stall
detector must auto-abort when the same 3-narration sequence repeats.
Source tests: routers/loop.py must route skip@ship to skip_at_ship and
expose POST /{loop_id}/approve-ship.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import loop_engine as le

ROUTER_SRC = Path("/app/backend/routers/loop.py").read_text(encoding="utf-8")


def _mk_engine():
    db = MagicMock()
    db.loop_sessions.update_one = AsyncMock()
    db.loop_events.insert_one = AsyncMock()
    db.loop_errors.insert_one = AsyncMock()
    db.loop_locks = MagicMock()
    db.loop_locks.delete_one = AsyncMock()
    eng = le.LoopEngine(db, "loop_t332", "user1", "proj1", "test task")
    return eng


async def _drain(eng):
    events = []
    while not eng.queue.empty():
        events.append(eng.queue.get_nowait())
    return events


class TestSkipAtShip:
    async def test_skip_at_ship_terminates_aborted(self):
        eng = _mk_engine()
        eng.state = le.LoopState.PAUSED_FOR_USER
        eng.phase = "ship"
        eng.context["ship_pending"] = {"owner": "o", "repo": "r",
                                       "branch": "main", "token": "t",
                                       "files": {}, "commit_message": "m"}
        await eng.skip_at_ship()
        assert eng.state == le.LoopState.ABORTED
        assert eng.context["terminal_reason"] == "SKIPPED_AT_SHIP"
        assert "ship_pending" not in eng.context

    async def test_skip_at_ship_emits_skipped_event(self):
        eng = _mk_engine()
        eng.state = le.LoopState.PAUSED_FOR_USER
        eng.phase = "ship"
        await eng.skip_at_ship()
        events = await _drain(eng)
        terminal = [e for e in events
                    if e["data"].get("kind") == "skipped_at_ship"]
        assert terminal, "must emit a skipped_at_ship terminal event"
        assert terminal[0]["state"] == "aborted"
        assert "SKIPPED_AT_SHIP" in terminal[0]["message"]

    async def test_skip_at_ship_idempotent_on_terminal(self):
        eng = _mk_engine()
        eng.state = le.LoopState.COMPLETED
        await eng.skip_at_ship()
        assert eng.state == le.LoopState.COMPLETED
        assert "terminal_reason" not in eng.context

    async def test_skip_at_ship_does_not_reenter_pipeline(self):
        """The old bug: skip resumed the pipeline (state back to
        EXECUTING). Post-fix the engine must be terminal + cancelled."""
        eng = _mk_engine()
        eng.state = le.LoopState.PAUSED_FOR_USER
        eng.phase = "ship"
        await eng.skip_at_ship()
        assert eng._cancelled is True
        assert eng._should_stop() is True


class TestStallDetector:
    async def test_repeated_3_sequence_auto_aborts(self):
        eng = _mk_engine()
        eng.state = le.LoopState.EXECUTING
        eng.phase = "execute"
        seq = [("execute", "pending", "Writing file A"),
               ("execute", "pending", "Verifying file A"),
               ("execute", "warning", "Retrying file A")]
        with pytest.raises(asyncio.CancelledError):
            for _round in range(2):
                for step, tone, text in seq:
                    await eng._narrate(step=step, tone=tone, text=text)
        assert eng.state == le.LoopState.FAILED
        assert "stall_auto_abort" in eng.context
        assert eng.context["stall_auto_abort"]["repeated_sequence"]

    async def test_non_repeating_sequence_does_not_abort(self):
        eng = _mk_engine()
        eng.state = le.LoopState.EXECUTING
        eng.phase = "execute"
        for i in range(10):
            await eng._narrate(step="execute", tone="pending",
                               text=f"Writing file {i}")
        assert eng.state == le.LoopState.EXECUTING

    async def test_terminal_state_never_stall_aborts(self):
        eng = _mk_engine()
        eng.state = le.LoopState.COMPLETED
        eng.phase = "ship"
        for _ in range(8):
            await eng._narrate(step="ship", tone="success", text="Done")
        assert eng.state == le.LoopState.COMPLETED


class TestRouterWiring:
    def test_pause_response_routes_skip_at_ship(self):
        seg = ROUTER_SRC.split("async def pause_response")[1]
        seg = seg.split("async def ")[0]
        assert "skip_at_ship" in seg
        assert 'engine.phase == "ship"' in seg
        # skip@ship branch must come BEFORE the generic retry/skip resume
        assert seg.index("skip_at_ship") < seg.index('("retry", "skip")')

    def test_approve_ship_endpoint_exists(self):
        assert '@router.post("/{loop_id}/approve-ship")' in ROUTER_SRC
        seg = ROUTER_SRC.split("async def approve_ship_endpoint")[1]
        seg = seg.split("async def ")[0]
        assert "confirm_ship_endpoint" in seg
        assert "approved=True" in seg

    def test_engine_has_skip_at_ship_method(self):
        assert hasattr(le.LoopEngine, "skip_at_ship")
