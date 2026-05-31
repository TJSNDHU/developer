"""
test_ship_turn_index.py — Iter 34 regression guard for the recurring
"Ship via CTO button reappears on refresh" bug.

Root cause (verified by reading code, Iter 34):
  • Frontend renders messages = [WELCOME, user_t1, asst_t1, user_t2, asst_t2,…]
    where WELCOME has provider='system' and is NOT persisted to the DB.
  • Old frontend code sent `turn_index = idx` from the rendered array.
  • So shipping the first assistant reply (rendered at idx=2) wrote to
    db.chat_sessions.turns[2].shipped_task_id, but the DB array only
    had 2 elements (indices 0, 1). MongoDB silently created a sparse
    third element {shipped_task_id} with no role/content. On reload,
    history returned 3 turns; the real assistant turn (now at idx=1
    because WELCOME isn't reloaded) had NO shipped_task_id, so the
    Ship button reappeared.

Backend fix (Iter 34):
  • /chat/turn/shipped now validates turn_index < len(turns). If the
    client sends a stale / off-by-one index, the backend falls back to
    marking the LATEST assistant turn as shipped instead of corrupting
    the doc with a sparse write.

This test locks both behaviours.
"""
from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

API = "http://localhost:8001/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "testpass123"


async def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


async def _login() -> str:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/auth/login",
                         json={"email": EMAIL, "password": PASSWORD})
        r.raise_for_status()
        return r.json()["token"]


@pytest.mark.asyncio
async def test_ship_with_correct_index_writes_to_assistant_turn():
    """Happy path: frontend sends a valid in-bounds index."""
    tok = await _login()
    db = await _db()
    sess_id = f"test-ship-{uuid.uuid4().hex[:8]}"
    now = time.time()
    # Seed a session with exactly 2 turns (user + assistant)
    user = await db.dev_users.find_one({"email": EMAIL}, {"user_id": 1})
    await db.chat_sessions.insert_one({
        "session_id": sess_id, "user_id": user["user_id"], "created_at": now,
        "turns": [
            {"role": "user", "content": "fix it", "ts": now},
            {"role": "assistant", "content": "Here's the plan", "ts": now + 1},
        ],
    })
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                f"{API}/chat/turn/shipped",
                headers={"Authorization": f"Bearer {tok}"},
                json={"session_id": sess_id, "turn_index": 1,
                      "task_id": "t-abc-123"},
            )
        assert r.status_code == 200
        assert r.json()["turn_index"] == 1
        # Verify DB write
        sess = await db.chat_sessions.find_one({"session_id": sess_id})
        assert sess["turns"][1]["shipped_task_id"] == "t-abc-123"
        # And critically — the array length is still 2, no sparse write
        assert len(sess["turns"]) == 2
    finally:
        await db.chat_sessions.delete_one({"session_id": sess_id})


@pytest.mark.asyncio
async def test_ship_with_out_of_bounds_index_falls_back_to_latest_assistant():
    """The bug scenario: frontend sends idx=2 (rendered position with
    WELCOME) but DB only has 2 turns. Backend MUST NOT create a sparse
    turns[2] — instead it falls back to the latest assistant turn."""
    tok = await _login()
    db = await _db()
    sess_id = f"test-ship-oob-{uuid.uuid4().hex[:8]}"
    now = time.time()
    user = await db.dev_users.find_one({"email": EMAIL}, {"user_id": 1})
    await db.chat_sessions.insert_one({
        "session_id": sess_id, "user_id": user["user_id"], "created_at": now,
        "turns": [
            {"role": "user", "content": "fix it", "ts": now},
            {"role": "assistant", "content": "Here's the plan", "ts": now + 1},
        ],
    })
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                f"{API}/chat/turn/shipped",
                headers={"Authorization": f"Bearer {tok}"},
                json={"session_id": sess_id, "turn_index": 5,  # WAY out of bounds
                      "task_id": "t-recovered"},
            )
        assert r.status_code == 200
        # Backend should have redirected the write to the latest assistant turn (idx=1)
        assert r.json()["turn_index"] == 1
        sess = await db.chat_sessions.find_one({"session_id": sess_id})
        # CRITICAL: array length unchanged (no sparse write)
        assert len(sess["turns"]) == 2
        assert sess["turns"][1]["shipped_task_id"] == "t-recovered"
        # And the impossible index didn't get populated
        for t in sess["turns"]:
            assert t.get("role") in ("user", "assistant")
    finally:
        await db.chat_sessions.delete_one({"session_id": sess_id})


@pytest.mark.asyncio
async def test_ship_rejects_negative_index():
    tok = await _login()
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{API}/chat/turn/shipped",
            headers={"Authorization": f"Bearer {tok}"},
            json={"session_id": "any", "turn_index": -1, "task_id": "t"},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_ship_rejects_unknown_session():
    tok = await _login()
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{API}/chat/turn/shipped",
            headers={"Authorization": f"Bearer {tok}"},
            json={"session_id": "no-such-session", "turn_index": 0, "task_id": "t"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ship_409_when_session_has_no_assistant_turns():
    """User-only session that happens to receive a stale ship request —
    backend must refuse rather than corrupt."""
    tok = await _login()
    db = await _db()
    sess_id = f"test-ship-noasst-{uuid.uuid4().hex[:8]}"
    user = await db.dev_users.find_one({"email": EMAIL}, {"user_id": 1})
    await db.chat_sessions.insert_one({
        "session_id": sess_id, "user_id": user["user_id"],
        "created_at": time.time(),
        "turns": [{"role": "user", "content": "hi", "ts": time.time()}],
    })
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                f"{API}/chat/turn/shipped",
                headers={"Authorization": f"Bearer {tok}"},
                json={"session_id": sess_id, "turn_index": 5, "task_id": "t"},
            )
        assert r.status_code == 409
    finally:
        await db.chat_sessions.delete_one({"session_id": sess_id})
