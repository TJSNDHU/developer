"""
test_loop_execute_stuck_recovery.py — Iter 308

BEHAVIOURAL regression coverage for the "loop stuck on execute for
2.5 hrs" user report on production (loop_643).

Root causes fixed by iter 308:
  1. `resume_stale()` ran ONLY at pod startup — an orphaned execute
     session sat at state="executing" forever if the pod stayed up.
     → Now runs every 60 s from a background task.
  2. `STALE_AFTER_S=300` was SHORTER than the execute phase budget
     (420 s), so a legitimate slow-but-progressing execute could get
     killed by the reaper before its own timeout fired.
     → Now `max(PHASE_TIMEOUTS_S.values()) + 60` = 480 s.
  3. During `generate_files` no progress event was emitted, so
     `last_event` in Mongo stayed on "EXECUTE START" for the entire
     phase — SSE clients on other workers (multi-worker prod)
     never saw progress.
     → Heartbeat task now emits every 10 s while gather() is in flight.

Tests here assert the OUTCOMES, not the mock plumbing:
  * `test_stale_executing_session_gets_rescued` — an "orphaned"
    session (state="executing", updated_at 10 min ago, no live
    engine object in _LIVE) gets flipped to `paused_for_user` by
    `resume_stale()`. Proves the reaper does the right thing when
    triggered — the "every 60s" cadence is a `main.py` lifespan
    concern, not something we mock the event loop for.
  * `test_stale_cutoff_is_greater_than_every_phase_budget` — the
    constant relationship that guarantees a reaper can never kill a
    legitimately-progressing phase. If someone bumps a phase budget
    in the future without adjusting STALE_AFTER_S, this test fails
    loudly.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services.loop_engine import (
    LoopState,
    PHASE_TIMEOUTS_S,
    STALE_AFTER_S,
    resume_stale,
)


def _db_client():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


@pytest.mark.asyncio
async def test_stale_executing_session_gets_rescued():
    """The exact user scenario: a session is stuck in state=executing
    with updated_at old enough to be considered orphaned. Reaper must
    flip it to paused_for_user so the frontend can render a real
    terminal state and the user can retry."""
    db = _db_client()
    loop_id = f"loop_test_stuck_{secrets.token_hex(4)}"
    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_S + 30)
    try:
        # Simulate the exact broken state from prod: a session left
        # over from a pipeline task that died silently. updated_at is
        # older than STALE_AFTER_S so the reaper considers it orphaned.
        await db.loop_sessions.insert_one({
            "loop_id":    loop_id,
            "user_id":    "u_test",
            "project_id": "p_test",
            "state":      LoopState.EXECUTING.value,
            "phase":      "execute",
            "context":    {"errors_encountered": []},
            "updated_at": stale_ts,
        })
        # Pre-condition: reaper sees the row and flips it.
        rescued = await resume_stale(db)
        assert rescued >= 1, (
            f"resume_stale should rescue the stuck session, "
            f"returned {rescued}"
        )
        doc = await db.loop_sessions.find_one(
            {"loop_id": loop_id},
            {"_id": 0, "state": 1, "resume_reason": 1},
        )
        assert doc["state"] == LoopState.PAUSED_FOR_USER.value, (
            f"expected paused_for_user, got {doc['state']}"
        )
        assert doc["resume_reason"] == "server_restart_mid_loop"
    finally:
        await db.loop_sessions.delete_many({"loop_id": loop_id})


@pytest.mark.asyncio
async def test_fresh_executing_session_is_not_touched_by_reaper():
    """The other half of correctness: a session that JUST started
    executing (updated_at 30 s ago) must NOT be flipped by the reaper.
    Prior bug: STALE_AFTER_S was 300 s < PHASE_TIMEOUTS_S['execute']
    (420 s), so a legitimate 6-minute execute got prematurely killed."""
    db = _db_client()
    loop_id = f"loop_test_fresh_{secrets.token_hex(4)}"
    fresh_ts = datetime.now(timezone.utc) - timedelta(seconds=30)
    try:
        await db.loop_sessions.insert_one({
            "loop_id":    loop_id,
            "user_id":    "u_test",
            "project_id": "p_test",
            "state":      LoopState.EXECUTING.value,
            "phase":      "execute",
            "context":    {"errors_encountered": []},
            "updated_at": fresh_ts,
        })
        await resume_stale(db)
        doc = await db.loop_sessions.find_one(
            {"loop_id": loop_id}, {"_id": 0, "state": 1},
        )
        # Still executing — reaper must NOT touch this row.
        assert doc["state"] == LoopState.EXECUTING.value, (
            f"reaper wrongly rescued a fresh session; state={doc['state']}"
        )
    finally:
        await db.loop_sessions.delete_many({"loop_id": loop_id})


def test_stale_cutoff_is_greater_than_every_phase_budget():
    """Guarantees the reaper can never kill a legitimately-progressing
    phase. This is the CONTRACT: STALE_AFTER_S > max phase budget.
    If someone bumps PHASE_TIMEOUTS_S['execute'] later without
    updating STALE_AFTER_S, this test fires."""
    max_phase = max(PHASE_TIMEOUTS_S.values())
    assert STALE_AFTER_S > max_phase, (
        f"STALE_AFTER_S ({STALE_AFTER_S}s) must exceed the largest "
        f"phase budget ({max_phase}s from PHASE_TIMEOUTS_S) — "
        "otherwise the reaper can kill a legitimately-progressing "
        "phase before its own timeout fires. This is the exact "
        "class of bug that produced iter 308's 2.5-hr stuck loop."
    )
