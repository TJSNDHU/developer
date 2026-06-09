"""
test_token_enforcement.py — covers the token-budget hard-stop, the
/usage/me endpoint, and the admin grant flow.

End-to-end against the live backend on localhost:8001.

Updated 2026-02-09: previous version used `test@aurem.dev` as the
target user, but iter 30 auto-promoted that email to the founder
allow-list (tier=founder, is_unlimited=true). Founders bypass the
budget entirely, so the exhaustion assertions could never pass.

New design: each test creates a throwaway free-tier user via
`/auth/signup`, runs assertions against THAT user, then cleans up.
The seeded `test@aurem.dev` account is still used — but only as the
ADMIN that calls the grant endpoint.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

API = "http://localhost:8001/api/aurem-dev"
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "testpass123"


async def _login(email: str, password: str) -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        tok = r.json()["token"]
        me = await c.get(f"{API}/usage/me", headers={"Authorization": f"Bearer {tok}"})
        me.raise_for_status()
        return tok, me.json()["user_id"]


async def _signup_throwaway() -> tuple[str, str, str, str]:
    """Create a fresh free-tier user. Returns (email, password, token, user_id)."""
    email = f"ci-throwaway-{uuid.uuid4().hex[:10]}@aurem-test.local"
    password = "ThrowawayPass123!"
    name = "CI Throwaway"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{API}/auth/signup",
            json={"email": email, "password": password, "name": name},
        )
        r.raise_for_status()
        body = r.json()
        tok = body["token"]
        uid = body["user_id"]
    return email, password, tok, uid


async def _db():
    url = os.environ["MONGO_URL"]
    name = os.environ.get("DB_NAME", "aurem_dev")
    return AsyncIOMotorClient(url)[name]


async def _purge_user(user_id: str) -> None:
    """Full cleanup of a throwaway user across all touched collections."""
    db = await _db()
    await db.cto_tasks.delete_many({"user_id": user_id})
    await db.cto_token_grants.delete_many({"user_id": user_id})
    await db.dev_users.delete_one({"user_id": user_id})


# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_usage_me_shape():
    """Fresh free-tier user has the expected /usage/me payload shape."""
    _, _, tok, uid = await _signup_throwaway()
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{API}/usage/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        body = r.json()
        for key in ("tier", "plan_limit", "tokens_granted", "effective_limit",
                    "used", "remaining", "pct_used", "is_exhausted"):
            assert key in body, f"missing {key}"
        assert body["tier"] == "free"
        assert body["plan_limit"] == 1000          # free tier per PLAN_LIMITS
        assert body["tokens_granted"] == 0
        assert body["is_exhausted"] is False
    finally:
        await _purge_user(uid)


@pytest.mark.asyncio
async def test_usage_me_unauthorized():
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{API}/usage/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_submit_402_when_exhausted_and_recovers_after_grant():
    """Burn budget → /cto/tasks/submit must 402 + NOT write a task row →
    admin grants tokens → next submit clears the 402 gate."""
    admin_tok, _admin_uid = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    _, _, user_tok, uid = await _signup_throwaway()
    db = await _db()

    try:
        # Burn the entire free budget via a single fake "done" task
        fake_id = f"t_test_enforce_{uuid.uuid4().hex[:8]}"
        await db.cto_tasks.insert_one({
            "task_id": fake_id, "user_id": uid, "status": "done",
            "tokens_used": 1500, "agent_used": "deepseek",
            "created_at": time.time(),
        })

        async with httpx.AsyncClient(timeout=10.0) as c:
            user_h = {"Authorization": f"Bearer {user_tok}"}
            admin_h = {"Authorization": f"Bearer {admin_tok}"}

            usage = (await c.get(f"{API}/usage/me", headers=user_h)).json()
            assert usage["is_exhausted"] is True, f"usage={usage}"

            # 1) submit must 402 (NOT touch AI / write to cto_tasks)
            before = await db.cto_tasks.count_documents({"user_id": uid})
            r = await c.post(
                f"{API}/cto/tasks/submit",
                headers=user_h,
                json={"project_id": "no-such", "task": "anything"},
            )
            assert r.status_code == 402, r.text
            d = r.json()["detail"]
            assert d["error"] == "token_limit_reached"
            assert d["used"] >= d["limit"]
            after = await db.cto_tasks.count_documents({"user_id": uid})
            assert after == before, "task row written despite 402"

            # 2) admin grants 800 → effective 1800 > 1500 used → recovered
            gr = await c.post(
                f"{API}/admin/users/{uid}/grant-tokens",
                headers=admin_h,
                json={"tokens": 800, "reason": "regression test"},
            )
            assert gr.status_code == 200, gr.text
            assert gr.json()["usage"]["is_exhausted"] is False

            # 3) submit now bypasses the budget check; 404 is fine (no project),
            #    what matters is NOT 402.
            r2 = await c.post(
                f"{API}/cto/tasks/submit",
                headers=user_h,
                json={"project_id": "no-such", "task": "anything"},
            )
            assert r2.status_code != 402, r2.text
    finally:
        await _purge_user(uid)


@pytest.mark.asyncio
async def test_grant_validation():
    """Admin grant endpoint validates input (zero, > 10M, missing user)."""
    admin_tok, _ = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    _, _, _, uid = await _signup_throwaway()
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            admin_h = {"Authorization": f"Bearer {admin_tok}"}

            # 0 / negative → 400
            r = await c.post(
                f"{API}/admin/users/{uid}/grant-tokens",
                headers=admin_h,
                json={"tokens": 0, "reason": "no"},
            )
            assert r.status_code == 400
            # > 10M → 400
            r = await c.post(
                f"{API}/admin/users/{uid}/grant-tokens",
                headers=admin_h,
                json={"tokens": 99_000_000, "reason": "abuse"},
            )
            assert r.status_code == 400
            # unknown user → 404
            r = await c.post(
                f"{API}/admin/users/nope_doesnt_exist/grant-tokens",
                headers=admin_h,
                json={"tokens": 100, "reason": "x"},
            )
            assert r.status_code == 404
    finally:
        await _purge_user(uid)
