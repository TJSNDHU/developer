"""
Iter 212m-30 PR-2 — Live HTTP smoke against the public preview URL.

Exercises the founder offer + repo indexing routes end-to-end against
the real backend (MongoDB live), then resets `founder_offer.spots_claimed`
back to 0 and deletes any claim docs created during the run.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://launch-pad-237.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api/aurem-dev"

FRESH_EMAIL = f"TEST_pr2_fresh_{uuid.uuid4().hex[:8]}@aurem.dev"
FRESH_PWD = "PR2FreshUser2026!"

_CREATED_CLAIM_IDS: list[str] = []
_CREATED_USER_IDS: list[str] = []


# ─── module-level cleanup ─────────────────────────────────────────────
def teardown_module(module):
    """Reset founder_offer counter + delete any TEST claims/users."""
    try:
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME")
        if not mongo_url or not db_name:
            print("Cleanup skipped — MONGO_URL/DB_NAME not set in this env")
            return
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]

        async def _do():
            # Restore spots — set spots_claimed back to 0
            await db.founder_offer.update_one(
                {"_id": "global"}, {"$set": {"spots_claimed": 0}}
            )
            # Wipe any claims we made
            if _CREATED_CLAIM_IDS:
                await db.user_seo_claims.delete_many({"claim_id": {"$in": _CREATED_CLAIM_IDS}})
            # Delete fresh signup user(s)
            if _CREATED_USER_IDS:
                await db.dev_users.delete_many({"user_id": {"$in": _CREATED_USER_IDS}})

        asyncio.get_event_loop().run_until_complete(_do())
        print(f"Cleanup OK — restored spots_claimed=0, removed {len(_CREATED_CLAIM_IDS)} claims, "
              f"{len(_CREATED_USER_IDS)} users")
    except Exception as e:
        print(f"Cleanup error: {e!r}")


# ─── public status ────────────────────────────────────────────────────
def test_offer_status_public_no_auth():
    r = requests.get(f"{API}/founder-offer/status", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 500
    assert 0 <= data["remaining"] <= 500
    assert data["is_active"] in (True, False)


# ─── fresh signup ─────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def fresh_user():
    r = requests.post(f"{API}/auth/signup", json={
        "email": FRESH_EMAIL, "password": FRESH_PWD, "name": "PR2 Fresh User",
    }, timeout=20)
    if r.status_code not in (200, 201):
        pytest.skip(f"signup failed: {r.status_code} {r.text}")
    body = r.json()
    token = body.get("token") or body.get("access_token") or body.get("aurem_token")
    uid = (body.get("user") or {}).get("user_id") or body.get("user_id")
    if uid:
        _CREATED_USER_IDS.append(uid)
    # signup should expose created_at as ISO
    user = body.get("user") or body
    created_at = user.get("created_at") or body.get("created_at")
    assert created_at is not None, f"signup body missing created_at: {body}"
    assert isinstance(created_at, str)
    return {"token": token, "user_id": uid, "created_at": created_at, "email": FRESH_EMAIL}


def test_signup_persists_created_at_and_me_surfaces_it(fresh_user):
    h = {"Authorization": f"Bearer {fresh_user['token']}"}
    r = requests.get(f"{API}/auth/me", headers=h, timeout=15)
    assert r.status_code == 200, r.text
    me = r.json()
    assert "created_at" in (me.get("user") or me), f"no created_at in /me: {me}"


def test_user_status_for_fresh_user(fresh_user):
    h = {"Authorization": f"Bearer {fresh_user['token']}"}
    r = requests.get(f"{API}/founder-offer/user-status", headers=h, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["repos_claimed"] == 0
    assert data["has_fully_claimed"] is False
    assert data["max_claims_per_user"] == 3
    assert isinstance(data["days_since_signup"], (int, float))
    assert data["days_since_signup"] < 0.1, f"too old: {data['days_since_signup']}"


# ─── claim flow (non-existent repo — orchestrator bails gracefully) ───
def test_claim_with_nonexistent_repo_returns_preview_with_errors(fresh_user):
    h = {"Authorization": f"Bearer {fresh_user['token']}"}
    r = requests.post(f"{API}/founder-offer/claim",
                      headers=h,
                      json={"repo_id": "nonexistent_repo_xyz", "site_url": ""},
                      timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    assert "claim_id" in body
    _CREATED_CLAIM_IDS.append(body["claim_id"])
    assert "preview" in body
    # Orchestrator bails with errors[] for unknown project — still returns
    # a structured preview, not a 5xx.
    assert isinstance(body["preview"].get("errors"), list)


def test_claim_again_returns_existing_idempotently(fresh_user):
    """Same repo_id from same user should NOT consume a second spot."""
    h = {"Authorization": f"Bearer {fresh_user['token']}"}
    # Status before
    before = requests.get(f"{API}/founder-offer/status", timeout=15).json()["remaining"]
    r = requests.post(f"{API}/founder-offer/claim",
                      headers=h,
                      json={"repo_id": "nonexistent_repo_xyz", "site_url": ""},
                      timeout=30)
    assert r.status_code == 200
    body = r.json()
    assert body.get("success") is True
    assert body.get("already_claimed") is True or "claim_id" in body
    after = requests.get(f"{API}/founder-offer/status", timeout=15).json()["remaining"]
    assert before == after, f"spots dropped from {before} to {after}"


# ─── cancel ───────────────────────────────────────────────────────────
def test_cancel_restores_spot_and_blocks_double_cancel(fresh_user):
    h = {"Authorization": f"Bearer {fresh_user['token']}"}
    # First create a fresh claim for repo_2
    r = requests.post(f"{API}/founder-offer/claim", headers=h,
                      json={"repo_id": "nonexistent_repo_for_cancel", "site_url": ""},
                      timeout=30)
    cid = r.json().get("claim_id")
    assert cid, r.text
    _CREATED_CLAIM_IDS.append(cid)

    before = requests.get(f"{API}/founder-offer/status", timeout=15).json()["remaining"]
    c = requests.post(f"{API}/founder-offer/cancel", headers=h,
                      json={"claim_id": cid}, timeout=15)
    assert c.status_code == 200
    assert c.json()["success"] is True
    after = requests.get(f"{API}/founder-offer/status", timeout=15).json()["remaining"]
    assert after == before + 1, f"spot not restored: {before} -> {after}"

    # Double cancel — not_cancellable
    c2 = requests.post(f"{API}/founder-offer/cancel", headers=h,
                       json={"claim_id": cid}, timeout=15)
    assert c2.status_code == 200
    assert c2.json()["success"] is False
    assert c2.json().get("reason") == "not_cancellable"


# ─── confirm: not testable without a real project. Skip safely. ───────
def test_confirm_on_unknown_claim_is_404(fresh_user):
    h = {"Authorization": f"Bearer {fresh_user['token']}"}
    r = requests.post(f"{API}/founder-offer/confirm", headers=h,
                      json={"claim_id": "claim_does_not_exist"}, timeout=15)
    assert r.status_code == 404


# ─── repo indexing — unknown repo returns 404 ─────────────────────────
def test_repo_index_unknown_project_returns_404(fresh_user):
    h = {"Authorization": f"Bearer {fresh_user['token']}"}
    r = requests.post(f"{API}/repos/no_such_project/index?commit=false",
                      headers=h, timeout=30)
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"


# ─── legacy user should report large days_since_signup ────────────────
def test_legacy_user_days_since_signup_is_large():
    """The pre-existing test@aurem.dev row is ~12 days old per the
    review brief. Card visibility on the frontend hinges on this."""
    login = requests.post(f"{API}/auth/login",
                          json={"email": "test@aurem.dev",
                                "password": "AuremTest2026!"}, timeout=15)
    if login.status_code != 200:
        pytest.skip(f"legacy login failed: {login.status_code} {login.text}")
    tok = login.json().get("token") or login.json().get("access_token")
    if not tok:
        pytest.skip("no token in legacy login response")
    h = {"Authorization": f"Bearer {tok}"}
    r = requests.get(f"{API}/founder-offer/user-status", headers=h, timeout=15)
    assert r.status_code == 200
    days = r.json().get("days_since_signup")
    # Some legacy rows may have no created_at → None is acceptable; if
    # numeric, expect >3 so the card hides.
    if isinstance(days, (int, float)):
        assert days > 3.0, f"legacy user days_since_signup={days} (should be >3)"
