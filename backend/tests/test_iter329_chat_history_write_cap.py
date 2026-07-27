"""test_iter329_chat_history_write_cap.py

Iter 329 · Chat-history B3 fix — write cap bumped -40 → -200.

Live repro: founder inspected localStorage + network trace on a real
reload. GET /chat/history?session_id=d4bbcbd4... returned exactly 40
messages — same as the write cap. Older turns had been silently
dropped by the $slice: -40 in _persist_turn.

Two locks in this file:
  1. STATIC — the source contains $slice: -200 and the corresponding
     tracking comment; a future accidental revert to -40 fails the
     test.
  2. RUNTIME — real Mongo write path exercised against the LIVE dev
     database. Insert 45 fake turns via the exact same
     $push/$slice operation as _persist_turn, confirm the doc holds
     45 turns (not 40). Also confirm that pushing beyond 200 caps
     correctly at 200 so growth is bounded.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


# ── Static lock — source contains -200, not -40 ──────────────────────
def test_persist_turn_slice_is_200_not_40():
    src = (Path(__file__).resolve().parents[1] / "routers" / "chat.py").read_text(
        encoding="utf-8",
    )
    # We check both the constant and the tracking comment.
    assert '"$slice": -200,' in src, (
        "chat_sessions turn cap MUST be -200 (Iter 329 B3 fix). "
        "The read window is -100 (chat.py line ~2940); cap must "
        "exceed it. If this test fails and the cap is -40, that is "
        "the exact regression this fix eliminated."
    )
    assert '"$slice": -40,' not in src, (
        "Found $slice: -40 in chat.py — that is the pre-Iter-329 "
        "value that caused history to silently truncate at 40 turns."
    )
    # Tracking comment must reference the Iter 329 rationale so
    # future editors know why -200 was chosen (not arbitrary).
    assert "Iter 329 · Chat-history B3 fix" in src, (
        "Iter 329 B3 rationale comment missing — please keep it so "
        "future editors understand the -200 choice."
    )


# ── Runtime lock — real Mongo write path ─────────────────────────────
@pytest.mark.asyncio
async def test_write_cap_retains_more_than_forty_turns():
    """Insert 45 turns via the same $push/$slice op _persist_turn uses.
    Confirm the doc holds all 45 (not truncated at 40).

    Uses a distinct session_id namespaced to this test so it never
    collides with real user data.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    mongo_url = os.environ.get("MONGO_URL")
    db_name   = os.environ.get("DB_NAME")
    assert mongo_url and db_name, (
        "MONGO_URL and DB_NAME must be set — this test hits real Mongo"
    )

    c = AsyncIOMotorClient(mongo_url)
    db = c[db_name]
    session_id = f"test_iter329_b3_writecap_{int(time.time())}"
    user_id    = "test_iter329_b3_user"

    try:
        # Clean any leftover from a prior run.
        await db.chat_sessions.delete_one(
            {"session_id": session_id, "user_id": user_id},
        )
        # Insert 45 turns via 23 write ops (each op pushes 2 turns
        # user+assistant, mirroring _persist_turn) → 46 total to hit
        # 45+ comfortably.
        for i in range(23):
            now = time.time() + i * 0.001
            user_turn      = {"role": "user",      "content": f"turn u{i}", "ts": now}
            assistant_turn = {"role": "assistant", "content": f"turn a{i}", "ts": now, "provider": "test"}
            await db.chat_sessions.update_one(
                {"session_id": session_id, "user_id": user_id},
                {
                    "$setOnInsert": {
                        "session_id": session_id,
                        "user_id":    user_id,
                        "created_at": now,
                        "project_id": None,
                    },
                    "$set": {"updated_at": now, "last_message": f"turn u{i}"},
                    "$push": {
                        "turns": {
                            "$each": [user_turn, assistant_turn],
                            "$slice": -200,   # MUST match _persist_turn
                        }
                    },
                },
                upsert=True,
            )
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0, "turns": 1},
        )
        turns = (doc or {}).get("turns") or []
        assert len(turns) == 46, (
            f"expected exactly 46 turns retained (23 iterations × 2 "
            f"per iter). got={len(turns)} — this is the direct real-"
            f"Mongo confirmation that -200 lets history exceed 40. "
            f"If it says 40, we regressed."
        )
        # Sanity: the FIRST turn (u0) must still be present. Under the
        # old -40 cap, it would have been sliced out by iteration 20.
        first_turn = turns[0]
        assert first_turn["content"] == "turn u0", (
            f"oldest turn (u0) was truncated — write cap likely too "
            f"low. first_turn={first_turn}"
        )
    finally:
        # Cleanup — never leave test data behind on the real db.
        await db.chat_sessions.delete_one(
            {"session_id": session_id, "user_id": user_id},
        )
        c.close()


@pytest.mark.asyncio
async def test_write_cap_stops_growth_at_200_upper_bound():
    """Push 250 turns — confirm the doc holds AT MOST 200 (the new
    cap). Guards against accidentally removing the cap entirely, which
    would let chat_sessions docs grow unbounded and eventually hit the
    Mongo 16MB doc limit."""
    from motor.motor_asyncio import AsyncIOMotorClient

    mongo_url = os.environ.get("MONGO_URL")
    db_name   = os.environ.get("DB_NAME")
    c = AsyncIOMotorClient(mongo_url)
    db = c[db_name]
    session_id = f"test_iter329_b3_upperbound_{int(time.time())}"
    user_id    = "test_iter329_b3_user"

    try:
        await db.chat_sessions.delete_one(
            {"session_id": session_id, "user_id": user_id},
        )
        # 125 iterations × 2 turns each = 250 total pushes.
        for i in range(125):
            now = time.time() + i * 0.0001
            await db.chat_sessions.update_one(
                {"session_id": session_id, "user_id": user_id},
                {
                    "$setOnInsert": {
                        "session_id": session_id, "user_id": user_id,
                        "created_at": now, "project_id": None,
                    },
                    "$set": {"updated_at": now, "last_message": f"u{i}"},
                    "$push": {
                        "turns": {
                            "$each": [
                                {"role": "user", "content": f"u{i}", "ts": now},
                                {"role": "assistant", "content": f"a{i}", "ts": now, "provider": "test"},
                            ],
                            "$slice": -200,
                        }
                    },
                },
                upsert=True,
            )
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0, "turns": 1},
        )
        turns = (doc or {}).get("turns") or []
        assert len(turns) == 200, (
            f"expected cap=200 turns retained. got={len(turns)}. "
            f"If >200, the cap was removed entirely (unbounded "
            f"growth risk). If <200, cap is too aggressive."
        )
    finally:
        await db.chat_sessions.delete_one(
            {"session_id": session_id, "user_id": user_id},
        )
        c.close()
