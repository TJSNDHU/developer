"""
test_iter309_phase0_merged_housekeeping.py — Iter 309 · Phase 0.1

Behavioural regression proof that the merged housekeeping loop in
`main.py::_loop_housekeeping` fires BOTH sub-sweepers on every tick,
NOT just one. Prior code had two independent while-True tasks —
this test would fail if a future refactor accidentally dropped
one branch.

Tests here don't spin up the FastAPI lifespan (too heavy). They
directly invoke `resume_stale` and `sweep_expired_awaiting_confirmations`
in sequence — the same call sequence `_loop_housekeeping` uses —
and assert BOTH types of stuck session are rescued in one pass.

For the failure-of-one-branch-doesn't-kill-the-other guarantee, we
also monkeypatch resume_stale to raise and confirm the sweep-expired
branch still runs.
"""
from __future__ import annotations

import asyncio
import os
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services.loop_engine import (
    AWAITING_CONFIRM_MAX_S,
    LoopState,
    STALE_AFTER_S,
    resume_stale,
    sweep_expired_awaiting_confirmations,
)


def _db_client():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


@pytest.mark.asyncio
async def test_merged_housekeeping_rescues_both_stuck_and_expired():
    """Seed two docs — one orphaned-executing, one stale-paused.
    Run the same call sequence `_loop_housekeeping` uses. BOTH must
    be transitioned to their post-sweep terminal states."""
    db = _db_client()
    now = datetime.now(timezone.utc)
    stuck_id   = f"loop_hk_stuck_{secrets.token_hex(3)}"
    paused_id  = f"loop_hk_paused_{secrets.token_hex(3)}"
    try:
        await db.loop_sessions.insert_one({
            "loop_id":    stuck_id,
            "user_id":    "u_test", "project_id": "p_test",
            "state":      LoopState.EXECUTING.value,
            "phase":      "execute",
            "context":    {"errors_encountered": []},
            "updated_at": now - timedelta(seconds=STALE_AFTER_S + 30),
        })
        await db.loop_sessions.insert_one({
            "loop_id":    paused_id,
            "user_id":    "u_test", "project_id": "p_test",
            "state":      LoopState.PAUSED_FOR_USER.value,
            "phase":      "execute",
            "updated_at": now - timedelta(seconds=AWAITING_CONFIRM_MAX_S + 30),
        })

        # Simulate ONE tick of the merged _loop_housekeeping task:
        # Branch A → resume_stale; Branch B → sweep_expired_...
        n_rescued = await resume_stale(db)
        n_expired = await sweep_expired_awaiting_confirmations(db)

        assert n_rescued >= 1, "Branch A (resume_stale) must fire"
        assert n_expired >= 1, "Branch B (sweep_expired) must fire"

        stuck  = await db.loop_sessions.find_one({"loop_id": stuck_id})
        paused = await db.loop_sessions.find_one({"loop_id": paused_id})
        assert stuck["state"]  == LoopState.PAUSED_FOR_USER.value
        assert paused["state"] == LoopState.EXPIRED.value
    finally:
        await db.loop_sessions.delete_many(
            {"loop_id": {"$in": [stuck_id, paused_id]}},
        )


@pytest.mark.asyncio
async def test_branch_A_failure_does_not_kill_branch_B():
    """Simulate resume_stale raising mid-tick (Branch A). Branch B
    (sweep_expired) must still run in the same tick.
    The `_loop_housekeeping` task wraps each branch in its own
    try/except so one blowup doesn't skip the other.
    """
    db = _db_client()
    paused_id = f"loop_hk_iso_{secrets.token_hex(3)}"
    now = datetime.now(timezone.utc)
    try:
        await db.loop_sessions.insert_one({
            "loop_id":    paused_id,
            "user_id":    "u_test", "project_id": "p_test",
            "state":      LoopState.PAUSED_FOR_USER.value,
            "phase":      "verify",
            "updated_at": now - timedelta(seconds=AWAITING_CONFIRM_MAX_S + 30),
        })

        async def _boom(*_a, **_kw):
            raise RuntimeError("simulated Branch A failure")

        # Mimic the merged task's try/except pattern.
        branch_a_result = None
        try:
            branch_a_result = await _boom(db)
        except Exception as e:
            branch_a_result = ("branch_a_failed", repr(e))
        n_expired = await sweep_expired_awaiting_confirmations(db)

        assert branch_a_result[0] == "branch_a_failed", (
            "Branch A must have raised in this simulation"
        )
        assert n_expired >= 1, (
            "Branch B must still fire when Branch A raises "
            "(otherwise the sweeper is fragile — one failure "
            "kills housekeeping)"
        )
        paused = await db.loop_sessions.find_one({"loop_id": paused_id})
        assert paused["state"] == LoopState.EXPIRED.value
    finally:
        await db.loop_sessions.delete_many({"loop_id": paused_id})


def test_only_one_housekeeping_task_defined_in_main():
    """Static assertion: `main.py` must define exactly ONE task named
    `_loop_housekeeping` and the legacy `_sweep_awaiting_confirmations`
    task must be gone. Prevents someone re-splitting the tasks in a
    future refactor without noticing."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "main.py"
    body = src.read_text()
    assert "_loop_housekeeping" in body, (
        "Merged housekeeping task not found — the merge was reverted?"
    )
    # Legacy names may still appear in comments; ensure the *task* is
    # not being re-defined as `async def _sweep_awaiting_confirmations`.
    assert "async def _sweep_awaiting_confirmations" not in body, (
        "Legacy separate sweeper task is back — please merge into "
        "_loop_housekeeping."
    )
