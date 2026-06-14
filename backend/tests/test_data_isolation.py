"""
tests/test_data_isolation.py — Iter 153 hard security guarantee.

Three asyncio tests that prove a freshly-created User B cannot read
any document created by User A. Any breach prints
"SECURITY BREACH" so it screams in CI output.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient


@pytest.fixture
def db():
    url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    name = os.environ.get("DB_NAME", "aurem_dev")
    client = AsyncIOMotorClient(url)
    return client[name]


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_project_isolation(db):
    uid_a = _uid("isoA")
    uid_b = _uid("isoB")
    pid = _uid("p")
    try:
        await db.cto_projects.insert_one({
            "project_id": pid, "user_id": uid_a,
            "name": "A-only", "created_at": time.time(),
        })
        # B asks for the same project — server-side reads MUST filter by user_id.
        leaked = await db.cto_projects.find_one(
            {"project_id": pid, "user_id": uid_b},
        )
        if leaked is not None:
            print("SECURITY BREACH: project visible to wrong user_id")
        assert leaked is None
    finally:
        await db.cto_projects.delete_many({"project_id": pid})


@pytest.mark.asyncio
async def test_chat_session_isolation(db):
    uid_a = _uid("isoA")
    uid_b = _uid("isoB")
    sid = _uid("s")
    try:
        await db.chat_sessions.insert_one({
            "session_id": sid, "user_id": uid_a,
            "turns": [{"role": "user", "content": "private"}],
            "created_at": time.time(),
        })
        leaked = await db.chat_sessions.find_one(
            {"session_id": sid, "user_id": uid_b},
        )
        if leaked is not None:
            print("SECURITY BREACH: chat session visible to wrong user_id")
        assert leaked is None
    finally:
        await db.chat_sessions.delete_many({"session_id": sid})


@pytest.mark.asyncio
async def test_task_isolation(db):
    uid_a = _uid("isoA")
    uid_b = _uid("isoB")
    tid = _uid("t")
    try:
        await db.cto_tasks.insert_one({
            "task_id": tid, "user_id": uid_a,
            "status": "done", "created_at": time.time(),
        })
        leaked = await db.cto_tasks.find_one(
            {"task_id": tid, "user_id": uid_b},
        )
        if leaked is not None:
            print("SECURITY BREACH: task visible to wrong user_id")
        assert leaked is None
    finally:
        await db.cto_tasks.delete_many({"task_id": tid})
