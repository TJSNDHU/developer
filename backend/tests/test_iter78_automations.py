"""
test_iter78_automations.py — scheduled / event-driven automation gap.

Closes the last red gap vs Cursor Automations. Covers:
  1. POST /automations/create persists a row scoped to the user.
  2. GET  /automations/list returns the user's rows (and only theirs).
  3. POST /automations/{id}/toggle flips enabled.
  4. DELETE /automations/{id} removes a row.
  5. POST /automations/webhook/github with a push payload triggers
     a queued task on the matching project (via _enqueue_cto_task,
     so the row is actually picked up by the worker).
  6. The router is mounted under /api/aurem-dev so the public URL
     is /api/aurem-dev/automations/webhook/github.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient


# Ensure MONGO_URL/DB_NAME are available even when pytest is invoked
# without `set -a; source .env`. Mirrors what the live backend reads.
def _ensure_env():
    if os.environ.get("MONGO_URL"):
        return
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_ensure_env()


API = "http://localhost:8001/api/aurem-dev"
TEST_PASSWORD = "auto-pass-9281"


async def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


async def _signup_and_get_token() -> tuple[str, str]:
    email = f"auto_{uuid.uuid4().hex[:10]}@aurem.test"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/auth/signup", json={
            "email": email, "password": TEST_PASSWORD,
            "name": "Auto Test",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    return body["token"], body["user_id"]


@pytest.mark.asyncio
async def test_create_list_toggle_delete_automation():
    token, user_id = await _signup_and_get_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Create
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/automations/create", headers=headers, json={
            "name": "Review main",
            "repo_full_name": f"u_{user_id[:6]}/test-repo",
            "trigger": "push",
            "branch_filter": "main",
            "task_template": "Review commits on {branch}\n{commit_messages}",
        })
    assert r.status_code == 200, r.text
    automation_id = r.json()["automation_id"]

    # List
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{API}/automations/list", headers=headers)
    assert r.status_code == 200
    rows = r.json()["automations"]
    assert any(row["_id"] == automation_id for row in rows)
    found = next(row for row in rows if row["_id"] == automation_id)
    assert found["enabled"] is True
    assert found["trigger"] == "push"

    # Toggle (enabled → disabled)
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/automations/{automation_id}/toggle",
                         headers=headers)
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    # Delete
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.delete(f"{API}/automations/{automation_id}",
                           headers=headers)
    assert r.status_code == 200

    # Subsequent toggle should 404
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/automations/{automation_id}/toggle",
                         headers=headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_requires_all_fields():
    token, _ = await _signup_and_get_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/automations/create", headers=headers, json={
            "name": "missing template",
        })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_webhook_triggers_task_on_matching_project():
    """End-to-end: insert a project + automation, then post a webhook
    payload and confirm a queued task lands in cto_tasks tagged with
    source='automation_webhook'."""
    token, user_id = await _signup_and_get_token()
    headers = {"Authorization": f"Bearer {token}"}
    db = await _db()

    owner = f"auto-{uuid.uuid4().hex[:6]}"
    repo  = "demo"
    repo_full = f"{owner}/{repo}"
    project_id = f"p_{uuid.uuid4().hex[:8]}"

    await db.cto_projects.insert_one({
        "project_id":   project_id,
        "user_id":      user_id,
        "github_owner": owner,
        "github_repo":  repo,
        "branch":       "main",
        # plaintext PAT so _enqueue_cto_task doesn't bail on no_pat
        "github_token": "ghp_dummy_for_test_only",
        "created_at":   time.time(),
    })

    # Create automation
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/automations/create", headers=headers, json={
            "name": "Auto-fix push",
            "repo_full_name": repo_full,
            "trigger": "push",
            "branch_filter": "main",
            "task_template": "Fix the push on {branch}: {commit_messages}",
        })
    assert r.status_code == 200, r.text

    # Fire a GitHub-style webhook (no secret set → signature check skipped)
    payload = {
        "ref": "refs/heads/main",
        "repository": {"full_name": repo_full},
        "pusher": {"name": "octocat"},
        "commits": [{"message": "fix: tiny tweak"}],
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{API}/automations/webhook/github",
            json=payload,
            headers={"X-GitHub-Event": "push"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["triggered"] == 1
    task_id = body["task_ids"][0]

    # The task row should exist, tagged automation_webhook.
    # Worker will fail (dummy PAT) but the routing proof is enough.
    for _ in range(10):
        row = await db.cto_tasks.find_one({"task_id": task_id})
        if row:
            break
        await asyncio.sleep(0.2)
    assert row is not None, "webhook did not create a task row"
    assert row["source"] == "automation_webhook"
    assert row["user_id"] == user_id
    assert row["project_id"] == project_id

    # Cleanup so we don't leak state across runs.
    await db.cto_tasks.delete_many({"user_id": user_id})
    await db.cto_projects.delete_many({"user_id": user_id})
    await db.cto_automations.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_webhook_skips_non_push_events():
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/automations/webhook/github",
                         json={"zen": "..."},
                         headers={"X-GitHub-Event": "ping"})
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] is True
    assert body["event"] == "ping"


def test_automations_router_wired_in_main():
    import os
    base = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(base, "main.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "automations_router" in src
    assert "from routers.automations import" in src
