"""
test_founder_and_admin_resilience.py — Iter 30 regressions.

Covers:
  1) Founder allow-list (teji.ss1986@gmail.com) auto-promotes to
     tier=founder + is_admin + is_unlimited on signup AND on login.
  2) `assert_has_budget` is a no-op for founders even when their
     cto_tasks burn already exceeds plan_limit.
  3) `/admin/me` works for a founder via the stale-JWT escape hatch
     even when the JWT itself was issued without is_admin.
"""
from __future__ import annotations

import asyncio
import os
import time

import httpx
import jwt
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

API = "http://localhost:8001/api/aurem-dev"
FOUNDER_EMAIL = "teji.ss1986@gmail.com"
FOUNDER_PASSWORD = "founder-test-pass-9281"


async def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


async def _reset_founder():
    db = await _db()
    await db.dev_users.delete_many({"email": FOUNDER_EMAIL})
    await db.cto_tasks.delete_many({"task_id": {"$regex": "^t_test_founder_"}})


@pytest.mark.asyncio
async def test_founder_signup_grants_unlimited():
    await _reset_founder()
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/auth/signup", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
            "name": "Founder Test",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tier"] == "founder"
    assert body["is_admin"] is True
    assert body["is_unlimited"] is True
    # JWT carries is_admin=true
    payload = jwt.decode(body["token"], os.environ["JWT_SECRET"], algorithms=["HS256"])
    assert payload["is_admin"] is True


@pytest.mark.asyncio
async def test_founder_login_promotes_existing_account():
    """Pre-existing free-tier row gets auto-promoted on next login."""
    await _reset_founder()
    db = await _db()
    # Seed as plain free user (no admin, no unlimited)
    import bcrypt, uuid
    hashed = bcrypt.hashpw(FOUNDER_PASSWORD.encode(), bcrypt.gensalt()).decode()
    await db.dev_users.insert_one({
        "user_id": uuid.uuid4().hex,
        "email": FOUNDER_EMAIL,
        "name": "Founder", "password": hashed,
        "tier": "free", "tokens_remaining": 1000,
    })
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/auth/login", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
        })
    assert r.status_code == 200
    body = r.json()
    assert body["is_admin"] is True
    assert body["is_unlimited"] is True
    assert body["tier"] == "founder"
    # DB row was written too
    row = await db.dev_users.find_one({"email": FOUNDER_EMAIL})
    assert row["is_admin"] is True
    assert row["tier"] == "founder"


@pytest.mark.asyncio
async def test_founder_never_exhausted_even_with_huge_burn():
    """Even if cto_tasks show tokens_used > 1B, /usage/me and submit
    must still permit the founder."""
    await _reset_founder()
    db = await _db()
    # Ensure the founder exists
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(f"{API}/auth/signup", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
        })
        login = await c.post(f"{API}/auth/login", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
        })
    tok = login.json()["token"]
    user_id = login.json()["user_id"]
    # Burn a ludicrous amount of tokens via fake done task
    await db.cto_tasks.insert_one({
        "task_id": "t_test_founder_burn", "user_id": user_id,
        "status": "done", "tokens_used": 9_999_999_999,
        "agent_used": "deepseek", "created_at": time.time(),
    })
    async with httpx.AsyncClient(timeout=10.0) as c:
        usage = (await c.get(f"{API}/usage/me",
                             headers={"Authorization": f"Bearer {tok}"})).json()
    assert usage["tier"] == "founder"
    assert usage["is_unlimited"] is True
    assert usage["is_exhausted"] is False
    # Submit should also bypass the budget check (404 is fine, 402 is NOT)
    async with httpx.AsyncClient(timeout=10.0) as c:
        sub = await c.post(
            f"{API}/cto/tasks/submit",
            headers={"Authorization": f"Bearer {tok}"},
            json={"project_id": "no-such", "task": "x"},
        )
    assert sub.status_code != 402, sub.text
    await _reset_founder()


@pytest.mark.asyncio
async def test_admin_me_works_with_stale_jwt():
    """Founder logs in once (gets is_admin=True JWT). We then forge an
    older JWT with is_admin=False on the same user_id and confirm
    /admin/me still works thanks to the DB fallback."""
    await _reset_founder()
    async with httpx.AsyncClient(timeout=10.0) as c:
        s = await c.post(f"{API}/auth/signup", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
        })
    user_id = s.json()["user_id"]
    # Forge a JWT WITHOUT is_admin to simulate a pre-promotion token
    stale = jwt.encode(
        {"user_id": user_id, "email": FOUNDER_EMAIL, "is_admin": False,
         "exp": int(time.time()) + 3600},
        os.environ["JWT_SECRET"], algorithm="HS256",
    )
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{API}/admin/me",
                        headers={"Authorization": f"Bearer {stale}"})
    assert r.status_code == 200, r.text
    assert r.json()["is_admin"] is True
    await _reset_founder()


@pytest.mark.asyncio
async def test_admin_me_rejects_plain_user():
    """A non-founder, non-admin user must still get 403."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        login = await c.post(f"{API}/auth/login", json={
            "email": "test@aurem.dev", "password": "testpass123",
        })
        if login.status_code != 200:
            pytest.skip("test user not seeded")
        tok = login.json()["token"]
        if login.json().get("is_admin"):
            pytest.skip("test user is admin in this env")
        r = await c.get(f"{API}/admin/me",
                        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
