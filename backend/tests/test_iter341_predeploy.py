"""Iter 341 pre-deploy verification for P0-1 (prompt-mode NoneType) and
P0-1b (history strips nulls). Hits PUBLIC REACT_APP_BACKEND_URL so we
verify what the user actually sees."""
import os
import time
import uuid

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

# Public preview URL (what the browser hits).
FE_ENV = "/app/frontend/.env"
_pub = None
with open(FE_ENV) as fh:
    for line in fh:
        if line.startswith("REACT_APP_BACKEND_URL="):
            _pub = line.split("=", 1)[1].strip()
BASE = f"{_pub}/api/aurem-dev"

NULL_SESSION = f"iter341-null-{uuid.uuid4().hex[:8]}"


async def _login(client):
    r = await client.post(f"{BASE}/auth/login", json={
        "email": "test@aurem.dev", "password": "AuremTest2026!",
    })
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _seed():
    from motor.motor_asyncio import AsyncIOMotorClient
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    now = time.time()
    await db.chat_sessions.delete_one({"session_id": NULL_SESSION})
    await db.chat_sessions.insert_one({
        "session_id": NULL_SESSION, "user_id": "test_admin_001",
        "created_at": now, "updated_at": now,
        "turns": [
            {"role": "user", "content": "q", "ts": now - 100},
            None,
            {"role": "assistant", "content": "a", "ts": now - 99,
             "provider": "openai"},
            None,
        ],
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", ["What is 2+2?", "hi"])
async def test_prompt_mode_no_nonetype(prompt):
    await _seed()
    async with httpx.AsyncClient(timeout=180, verify=True) as client:
        token = await _login(client)
        body = ""
        async with client.stream(
            "POST", f"{BASE}/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt": prompt, "session_id": NULL_SESSION},
        ) as r:
            assert r.status_code == 200, await r.aread()
            async for chunk in r.aiter_text():
                body += chunk
                if len(body) > 400_000:
                    break
    assert "NoneType" not in body, body[:1500]
    assert "'NoneType' object has no attribute" not in body, body[:1500]
    assert (
        '"token"' in body
        or "wasn't able to produce a reply" in body
    ), body[:1500]


@pytest.mark.asyncio
async def test_history_strips_nulls_public():
    await _seed()
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _login(client)
        r = await client.get(
            f"{BASE}/chat/history",
            params={"session_id": NULL_SESSION},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        msgs = r.json().get("messages", [])
        assert msgs, "history should contain the two valid turns"
        for m in msgs:
            assert isinstance(m, dict), f"non-dict turn leaked: {m!r}"
            assert m.get("role"), f"role missing on: {m!r}"
