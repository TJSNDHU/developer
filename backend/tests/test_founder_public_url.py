"""Verify founder + admin resilience over the PUBLIC URL (Kubernetes ingress)."""
from __future__ import annotations

import os
import time

import httpx
import jwt
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://launch-pad-237.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api/aurem-dev"
FOUNDER_EMAIL = "teji.ss1986@gmail.com"
FOUNDER_PASSWORD = "FounderOwn123!"


async def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "aurem_dev")]


async def _reset():
    db = await _db()
    await db.dev_users.delete_many({"email": FOUNDER_EMAIL})
    await db.cto_tasks.delete_many({"task_id": {"$regex": "^t_pub_founder_"}})


@pytest.mark.asyncio
async def test_public_founder_signup_unlimited():
    await _reset()
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(f"{API}/auth/signup", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD, "name": "Founder",
        })
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["tier"] == "founder"
    assert b["is_admin"] is True
    assert b["is_unlimited"] is True


@pytest.mark.asyncio
async def test_public_founder_login_promotes_stale():
    """Pre-seed free row, then login over public URL must promote it."""
    await _reset()
    db = await _db()
    import bcrypt, uuid
    hashed = bcrypt.hashpw(FOUNDER_PASSWORD.encode(), bcrypt.gensalt()).decode()
    await db.dev_users.insert_one({
        "user_id": uuid.uuid4().hex, "email": FOUNDER_EMAIL,
        "name": "Founder", "password": hashed,
        "tier": "free", "is_admin": False, "tokens_remaining": 1000,
    })
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(f"{API}/auth/login", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
        })
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["tier"] == "founder"
    assert b["is_admin"] is True
    assert b["is_unlimited"] is True
    row = await db.dev_users.find_one({"email": FOUNDER_EMAIL})
    assert row["tier"] == "founder"
    assert row["is_admin"] is True


@pytest.mark.asyncio
async def test_public_founder_never_exhausted():
    await _reset()
    async with httpx.AsyncClient(timeout=20.0) as c:
        await c.post(f"{API}/auth/signup", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
        })
        login = await c.post(f"{API}/auth/login", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
        })
    tok = login.json()["token"]
    user_id = login.json()["user_id"]
    db = await _db()
    await db.cto_tasks.insert_one({
        "task_id": "t_pub_founder_burn", "user_id": user_id,
        "status": "done", "tokens_used": 9_999_999_999,
        "agent_used": "deepseek", "created_at": time.time(),
    })
    async with httpx.AsyncClient(timeout=20.0) as c:
        usage = (await c.get(f"{API}/usage/me",
                             headers={"Authorization": f"Bearer {tok}"})).json()
        sub = await c.post(f"{API}/cto/tasks/submit",
                           headers={"Authorization": f"Bearer {tok}"},
                           json={"project_id": "no-such", "task": "x"})
    assert usage["is_unlimited"] is True
    assert usage["is_exhausted"] is False
    assert sub.status_code != 402, f"Founder got 402: {sub.text}"


@pytest.mark.asyncio
async def test_public_admin_me_with_stale_jwt():
    await _reset()
    async with httpx.AsyncClient(timeout=20.0) as c:
        s = await c.post(f"{API}/auth/signup", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
        })
    user_id = s.json()["user_id"]
    stale = jwt.encode(
        {"user_id": user_id, "email": FOUNDER_EMAIL, "is_admin": False,
         "exp": int(time.time()) + 3600},
        os.environ["JWT_SECRET"], algorithm="HS256",
    )
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(f"{API}/admin/me",
                        headers={"Authorization": f"Bearer {stale}"})
    assert r.status_code == 200, r.text
    assert r.json()["is_admin"] is True


@pytest.mark.asyncio
async def test_public_regression_non_founder_402_still_works():
    """test@aurem.dev should still hit 402 once exhausted."""
    async with httpx.AsyncClient(timeout=20.0) as c:
        login = await c.post(f"{API}/auth/login", json={
            "email": "test@aurem.dev", "password": "testpass123",
        })
        if login.status_code != 200:
            pytest.skip("test@aurem.dev not seeded")
        tok = login.json()["token"]
        user_id = login.json()["user_id"]
    db = await _db()
    await db.cto_tasks.delete_many({"task_id": {"$regex": "^t_pub_regress_"}})
    await db.cto_tasks.insert_one({
        "task_id": "t_pub_regress_burn", "user_id": user_id,
        "status": "done", "tokens_used": 2000,
        "agent_used": "deepseek", "created_at": time.time(),
    })
    async with httpx.AsyncClient(timeout=20.0) as c:
        usage = (await c.get(f"{API}/usage/me",
                             headers={"Authorization": f"Bearer {tok}"})).json()
        sub = await c.post(f"{API}/cto/tasks/submit",
                           headers={"Authorization": f"Bearer {tok}"},
                           json={"project_id": "no-such", "task": "x"})
    # Cleanup
    await db.cto_tasks.delete_many({"task_id": {"$regex": "^t_pub_regress_"}})
    if usage.get("is_unlimited"):
        pytest.skip("test user is marked unlimited in this env")
    assert usage["is_exhausted"] is True, usage
    assert sub.status_code == 402, sub.text


@pytest.mark.asyncio
async def test_cleanup_founder_user():
    """Final cleanup as requested in handoff."""
    await _reset()
