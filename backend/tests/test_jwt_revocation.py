"""
test_jwt_revocation.py — Iter 307

BEHAVIOURAL tests for the JWT revocation store + per-user session barrier.

  1. valid_token_works_before_logout   — baseline: /auth/me returns 200
  2. same_token_rejected_after_logout  — the exact failure the entire
     feature exists to prevent. A stolen token replayed after
     /auth/logout must 401.
  3. ttl_index_is_installed            — the Mongo TTL index really
     exists on `revoked_tokens.expires_at` with expireAfterSeconds=0,
     so entries auto-clean at natural JWT expiry. Cannot wait 7 days
     to prove full expiry — asserting the mechanism is wired.
  4. revoke_all_sessions_kills_every_token — issue two independent
     tokens for the same user, call /auth/revoke-all-sessions, both
     tokens 401 on the next request.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import time

import httpx
import jwt
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

API = "http://localhost:8001/api/aurem-dev"


def _fresh_email() -> str:
    """Unique per test run to avoid dev_users collisions."""
    return f"revoke-test-{secrets.token_hex(4)}@aurem.test"


async def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


async def _signup_and_login(client, email: str) -> str:
    """Return a valid JWT for a freshly-created user."""
    r = await client.post(f"{API}/auth/signup", json={
        "email": email, "password": "TestPass2026!",
        "name": "Revoke Test User",
    })
    assert r.status_code == 200, f"signup failed: {r.text}"
    tok = r.json()["token"]
    assert tok
    return tok


async def _cleanup_user(email: str) -> None:
    db = await _db()
    row = await db.dev_users.find_one({"email": email}, {"user_id": 1})
    await db.dev_users.delete_many({"email": email})
    if row:
        await db.revoked_tokens.delete_many({"user_id": row.get("user_id")})


@pytest.mark.asyncio
async def test_valid_token_works_before_logout():
    """Baseline: a freshly-issued token succeeds on /auth/me."""
    email = _fresh_email()
    async with httpx.AsyncClient(timeout=10.0) as c:
        try:
            tok = await _signup_and_login(c, email)
            r = await c.get(
                f"{API}/auth/me",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["user"]["email"] == email
        finally:
            await _cleanup_user(email)


@pytest.mark.asyncio
async def test_same_token_rejected_after_logout():
    """THE headline behaviour: /auth/logout revokes the specific jti,
    so replaying the same token gets a 401 with a `revoked` detail
    (not a generic 'expired' or 'invalid signature')."""
    email = _fresh_email()
    async with httpx.AsyncClient(timeout=10.0) as c:
        try:
            tok = await _signup_and_login(c, email)
            headers = {"Authorization": f"Bearer {tok}"}
            # Sanity: token works before logout.
            pre = await c.get(f"{API}/auth/me", headers=headers)
            assert pre.status_code == 200, pre.text

            # Logout: server should insert this token's jti into
            # revoked_tokens and return ok:true, revoked:true.
            out = await c.post(f"{API}/auth/logout", headers=headers)
            assert out.status_code == 200, out.text
            body = out.json()
            assert body["ok"] is True
            assert body["revoked"] is True
            assert "jti_last6" in body

            # Now the same token must 401.
            post = await c.get(f"{API}/auth/me", headers=headers)
            assert post.status_code == 401, post.text
            assert "revoked" in (post.json().get("detail") or "").lower(), (
                f"expected 'revoked' in detail, got: {post.text}"
            )

            # And the revocation row is really in Mongo.
            payload = jwt.decode(
                tok, os.environ["JWT_SECRET"], algorithms=["HS256"],
            )
            db = await _db()
            doc = await db.revoked_tokens.find_one({"jti": payload["jti"]})
            assert doc is not None
            assert doc["reason"] == "logout"
            assert doc["user_id"] == payload["user_id"]
            # expires_at must equal the JWT's exp claim (± clock skew)
            # so Mongo's TTL monitor cleans it up at natural expiry.
            assert abs(
                doc["expires_at"].timestamp() - payload["exp"]
            ) < 2, "expires_at must match JWT exp so TTL trims correctly"
        finally:
            await _cleanup_user(email)


@pytest.mark.asyncio
async def test_ttl_index_is_installed_on_revoked_tokens():
    """Proves the Mongo TTL mechanism is wired. We CAN'T wait 7 days
    to observe an auto-delete, so we assert the index config directly.
    If this assertion holds, MongoDB WILL delete each revoked-token
    document within ~60s of its `expires_at` timestamp — that's a
    guarantee from the DB layer we can rely on."""
    db = await _db()
    idxs = await db.revoked_tokens.index_information()
    ttl_idx = next(
        (v for v in idxs.values()
         if v.get("expireAfterSeconds") is not None
         and ("expires_at", 1) in v.get("key", [])),
        None,
    )
    assert ttl_idx is not None, (
        f"No TTL index found on revoked_tokens.expires_at. Existing: {idxs}"
    )
    assert ttl_idx["expireAfterSeconds"] == 0, (
        "expireAfterSeconds must be 0 so Mongo expires docs AT the "
        f"stored timestamp, got {ttl_idx['expireAfterSeconds']}."
    )
    # Also verify the fast-lookup jti index exists (hot-path).
    assert any(
        ("jti", 1) in v.get("key", []) for v in idxs.values()
    ), f"No index on revoked_tokens.jti. Existing: {idxs}"


@pytest.mark.asyncio
async def test_revoke_all_sessions_kills_every_token_for_user():
    """Founder-nuke flow: two independent tokens for the same user
    (issued in sequence) both start rejecting after a single call to
    /auth/revoke-all-sessions. Proves the per-user `session_barrier_at`
    barrier works and doesn't need to enumerate individual jtis."""
    email = _fresh_email()
    async with httpx.AsyncClient(timeout=10.0) as c:
        try:
            # First token from signup.
            tok_a = await _signup_and_login(c, email)
            # Sleep 1s so the SECOND token has a DIFFERENT iat, but
            # both are BEFORE the barrier we'll set below.
            await asyncio.sleep(1.1)
            # Second token via /auth/login — different jti, different
            # iat, same user_id.
            login_r = await c.post(f"{API}/auth/login", json={
                "email": email, "password": "TestPass2026!",
            })
            assert login_r.status_code == 200, login_r.text
            tok_b = login_r.json()["token"]
            assert tok_a != tok_b, "second login must issue a fresh token"

            # Both live.
            pre_a = await c.get(f"{API}/auth/me",
                                headers={"Authorization": f"Bearer {tok_a}"})
            pre_b = await c.get(f"{API}/auth/me",
                                headers={"Authorization": f"Bearer {tok_b}"})
            assert pre_a.status_code == 200 and pre_b.status_code == 200

            # User revokes their own sessions via the self-nuke branch
            # of the endpoint (uses tok_b as auth — will die too).
            user_id = pre_b.json()["user"]["user_id"]
            # Wait ≥1s so the barrier `now` is STRICTLY greater than
            # both tokens' iat claims (which are integer-second unix).
            await asyncio.sleep(1.1)
            nuke = await c.post(
                f"{API}/auth/revoke-all-sessions",
                headers={"Authorization": f"Bearer {tok_b}"},
                json={"user_id": user_id, "reason": "test_self_nuke"},
            )
            assert nuke.status_code == 200, nuke.text
            assert nuke.json()["sessions_nuked"] == 1
            assert nuke.json()["actor"] == "self"

            # Both tokens must 401 now (barrier check catches iat < now).
            post_a = await c.get(f"{API}/auth/me",
                                 headers={"Authorization": f"Bearer {tok_a}"})
            post_b = await c.get(f"{API}/auth/me",
                                 headers={"Authorization": f"Bearer {tok_b}"})
            assert post_a.status_code == 401, post_a.text
            assert post_b.status_code == 401, post_b.text
            # Both should carry the barrier-specific detail, not
            # jti-revoked (since we set the barrier, not the jti).
            for r in (post_a, post_b):
                d = (r.json().get("detail") or "").lower()
                assert "all sessions revoked" in d, r.text

            # A fresh login AFTER the barrier must work — the barrier
            # is a wall, not a permanent ban.
            await asyncio.sleep(1.1)
            fresh = await c.post(f"{API}/auth/login", json={
                "email": email, "password": "TestPass2026!",
            })
            assert fresh.status_code == 200, fresh.text
            tok_c = fresh.json()["token"]
            after = await c.get(f"{API}/auth/me",
                                headers={"Authorization": f"Bearer {tok_c}"})
            assert after.status_code == 200, after.text
        finally:
            await _cleanup_user(email)
