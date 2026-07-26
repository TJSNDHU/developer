"""
Iter 212m-210 — Ask Advisor context RBAC tier split regression.

Contract locked in by this test:

* Founder view (is_admin | tier=="founder" | FOUNDER_EMAILS) receives
  `role="founder"` and BOTH `council` and `deploy_sync` keys.
* Non-founder view receives `role="user"` and neither `council` nor
  `deploy_sync` appear on the wire (not even as `null`).
* Ownership rule is unchanged — cross-user access still 404s.
* chat.py prompt injector keeps the INFRA GUARD rule (locked by
  source-string presence so a refactor can't silently delete it).

The test hits the live supervisor-managed backend via
REACT_APP_BACKEND_URL so we exercise the same DB & auth stack that
production uses.  It seeds its own throwaway rows in Mongo and
cleans up at the end.
"""

from __future__ import annotations

import os
import uuid

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL"):
                    BASE_URL = line.split("=", 1)[1].strip()
                    break
    except FileNotFoundError:
        pass

# Iter 309 · Phase 0.2 · Round 4 — see identical fix in
# tests/test_aurem_backend.py. Bare `assert` raises at collection
# time and aborts the WHOLE pytest run when the env var is missing;
# use pytest.skip so this file is cleanly skipped in CI without
# affecting collection of every other test.
if not BASE_URL:
    pytest.skip("REACT_APP_BACKEND_URL not set — skipping live-URL smoke tests",
                allow_module_level=True)
BASE_URL = BASE_URL.rstrip("/")
AUREM = f"{BASE_URL}/api/aurem-dev"

FOUNDER_EMAIL = "test@aurem.dev"
FOUNDER_PASSWORD = "AuremTest2026!"


pytestmark = pytest.mark.asyncio


async def _db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return c[os.environ["DB_NAME"]]


def _login(email: str, password: str) -> tuple[str, str]:
    """Return (user_id, jwt). Raises on failure so the test surfaces
    the exact 4xx/5xx status."""
    r = requests.post(
        f"{AUREM}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    r.raise_for_status()
    d = r.json()
    return d["user_id"], d["token"]


def _signup(email: str, password: str) -> tuple[str, str]:
    r = requests.post(
        f"{AUREM}/auth/signup",
        json={"email": email, "password": password, "name": "Tier1 Probe"},
        timeout=15,
    )
    r.raise_for_status()
    d = r.json()
    return d["user_id"], d["token"]


async def _seed_project(user_id: str, tag: str) -> str:
    db = await _db()
    pid = f"p_tier_probe_{tag}_{uuid.uuid4().hex[:6]}"
    await db.cto_projects.insert_one({
        "project_id": pid,
        "user_id": user_id,
        "name": f"probe-{tag}",
    })
    await db.cto_open_findings.insert_many([
        {"project_id": pid, "user_id": user_id, "finding_id": "f1",
         "severity": "P0", "status": "open"},
        {"project_id": pid, "user_id": user_id, "finding_id": "f2",
         "severity": "P2", "status": "open"},
    ])
    return pid


async def _cleanup(user_id: str | None, project_id: str, *, drop_user: bool):
    db = await _db()
    await db.cto_projects.delete_many({"project_id": project_id})
    await db.cto_open_findings.delete_many({"project_id": project_id})
    if drop_user and user_id:
        await db.dev_users.delete_many({"user_id": user_id})


def _ctx(token: str, project_id: str) -> requests.Response:
    return requests.get(
        f"{AUREM}/advisor/context",
        params={"project_id": project_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )


async def test_founder_sees_all_tiers():
    uid, tok = _login(FOUNDER_EMAIL, FOUNDER_PASSWORD)
    pid = await _seed_project(uid, "founder")
    try:
        r = _ctx(tok, pid)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["role"] == "founder"
        assert "council" in body, "founders must see council"
        assert "deploy_sync" in body, "founders must see deploy_sync"
        assert body["findings"]["p0"] == 1
        assert body["findings"]["p2"] == 1
    finally:
        await _cleanup(None, pid, drop_user=False)


async def test_non_founder_hides_infra():
    email = f"tier1_probe_{uuid.uuid4().hex[:8]}@example.com"
    uid, tok = _signup(email, "TestPass123!")
    pid = await _seed_project(uid, "user")
    try:
        r = _ctx(tok, pid)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["role"] == "user"
        # Hard fail if infra fingerprints leak to a normal user — the
        # whole reason this iteration exists.
        assert "council" not in body, f"council leaked to non-founder: {body}"
        assert "deploy_sync" not in body, f"deploy_sync leaked: {body}"
        assert body["findings"]["p0"] == 1
        assert body["findings"]["p2"] == 1
        assert "quota" in body
    finally:
        await _cleanup(uid, pid, drop_user=True)


async def test_cross_user_ownership_still_404():
    # Founder-owned project + non-founder caller must 404, even after
    # the RBAC split (the split shapes fields, not ownership).
    f_uid, _ = _login(FOUNDER_EMAIL, FOUNDER_PASSWORD)
    f_pid = await _seed_project(f_uid, "cross")
    email = f"tier1_probe_{uuid.uuid4().hex[:8]}@example.com"
    u_uid, u_tok = _signup(email, "TestPass123!")
    try:
        r = _ctx(u_tok, f_pid)
        assert r.status_code == 404
    finally:
        await _cleanup(u_uid, f_pid, drop_user=True)


def test_chat_prompt_has_infra_guard_source():
    """Prompt-injection rule is enforced by source inspection so a
    future refactor can't quietly delete it and pass CI."""
    src = open("/app/backend/routers/chat.py", encoding="utf-8").read()
    assert "INFRA GUARD" in src, "chat.py lost the non-founder infra guard rule"
    assert "_is_founder_view" in src, \
        "chat.py must gate council/deploy prompt lines by founder role"
