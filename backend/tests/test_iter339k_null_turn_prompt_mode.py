"""Iter 339k — PROD P0 regression: prompt-mode chat crashed with
"'NoneType' object has no attribute 'get'" when the session's turns
array contained literal nulls (Mongo index-padding). Sends a trivial
2-char message through the REAL /chat/stream path on a null-poisoned
session and asserts the raw NoneType error never appears."""
import asyncio
import json
import os
import time
import uuid

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = "http://localhost:8001/api/aurem-dev"
NULL_SESSION = f"iter339k-null-{uuid.uuid4().hex[:8]}"


async def _login(client):
    r = await client.post(f"{BASE}/auth/login", json={
        "email": "test@aurem.dev", "password": "AuremTest2026!",
    })
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _seed_null_turns():
    from motor.motor_asyncio import AsyncIOMotorClient
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    now = time.time()
    await db.chat_sessions.delete_one({"session_id": NULL_SESSION})
    await db.chat_sessions.insert_one({
        "session_id": NULL_SESSION, "user_id": "test_admin_001",
        "created_at": now, "updated_at": now,
        "turns": [
            {"role": "user", "content": "old q", "ts": now - 100},
            None,
            {"role": "assistant", "content": "old a", "ts": now - 99,
             "provider": "openai"},
            None,
        ],
    })


@pytest.mark.asyncio
async def test_trivial_prompt_survives_null_turns():
    await _seed_null_turns()
    async with httpx.AsyncClient(timeout=120) as client:
        token = await _login(client)
        body = ""
        async with client.stream(
            "POST", f"{BASE}/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt": "hi", "session_id": NULL_SESSION},
        ) as r:
            assert r.status_code == 200, await r.aread()
            async for chunk in r.aiter_text():
                body += chunk
                if len(body) > 400_000:
                    break
    # The exact prod symptom must never appear anywhere in the stream.
    assert "'NoneType' object has no attribute" not in body, body[:2000]
    assert "NoneType" not in body, body[:2000]
    # Stream must produce SOMETHING renderable: either real tokens or
    # the graceful fallback — never a bare raw-error frame.
    assert '"token"' in body or "wasn't able to produce a reply" in body, body[:2000]


@pytest.mark.asyncio
async def test_history_endpoint_strips_null_turns():
    await _seed_null_turns()
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _login(client)
        r = await client.get(
            f"{BASE}/chat/history", params={"session_id": NULL_SESSION},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        msgs = r.json().get("messages", [])
        assert all(isinstance(m, dict) and m.get("role") for m in msgs)
