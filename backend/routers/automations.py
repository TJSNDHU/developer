"""
routers/automations.py — scheduled + event-driven automations.

Closes the last red gap vs Cursor Automations. Triggers:
  • GitHub push webhook (`POST /automations/webhook/github`)
  • Manual run-now button (`POST /automations/{id}/run`)
  • Cron schedule (stored only — driver lives in a future iter)

Setup for a repo:
  GitHub → Settings → Webhooks → Add webhook
    Payload URL: https://<your-domain>/api/aurem-dev/automations/webhook/github
    Content type: application/json
    Secret: same string as the `GITHUB_WEBHOOK_SECRET` env var
    Events: just the push event
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, Request

from cto_services.auth import current_dev
from cto_services.db import require_db

router = APIRouter(prefix="/automations", tags=["Automations"])

GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")


@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None,
                                                alias="X-Hub-Signature-256"),
    x_github_event: str = Header("push", alias="X-GitHub-Event"),
) -> dict:
    """GitHub posts push events here.  Each push triggers any matching
    automations whose `repo_full_name` + `branch_filter` match."""
    payload = await request.body()

    if GITHUB_WEBHOOK_SECRET:
        expected = "sha256=" + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode("utf-8"),
            payload, hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, x_hub_signature_256 or ""):
            raise HTTPException(401, "Invalid webhook signature")

    if x_github_event not in ("push", "pull_request"):
        return {"ok": True, "skipped": True, "event": x_github_event}

    try:
        data = json.loads(payload or b"{}")
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    repo_full = (data.get("repository") or {}).get("full_name") or ""
    branch    = (data.get("ref") or "").replace("refs/heads/", "")
    pusher    = (data.get("pusher") or {}).get("name") or ""
    commits   = data.get("commits") or []

    if not repo_full or not commits:
        return {"ok": True, "skipped": True, "reason": "no commits"}

    db = require_db()
    rules = await db.cto_automations.find({
        "repo_full_name": repo_full,
        "trigger":        "push",
        "enabled":        True,
    }).to_list(20)
    rules = [r for r in rules
             if not r.get("branch_filter") or r["branch_filter"] == branch]

    triggered: list[str] = []
    commit_msgs = "\n".join(
        f"- {(c.get('message') or '')[:80]}" for c in commits[:3]
    )

    # Lazy import to avoid circular routers <-> services on app boot.
    from routers.cto_projects import _enqueue_cto_task

    for rule in rules:
        try:
            description = rule["task_template"].format(
                branch=branch, pusher=pusher, repo=repo_full,
                commit_count=len(commits),
                commit_messages=commit_msgs,
            )
        except KeyError:
            description = rule["task_template"]
        proj = await db.cto_projects.find_one({
            "user_id":       rule["user_id"],
            "github_owner":  repo_full.split("/", 1)[0],
            "github_repo":   repo_full.split("/", 1)[-1],
        })
        if not proj:
            continue
        # Reuse the canonical enqueue path so the background worker
        # actually runs (clone → AI fix → push), instead of leaving the
        # row stuck on "queued" forever.
        res = await _enqueue_cto_task(
            user_id=rule["user_id"],
            project_id=proj.get("project_id"),
            task_text=description,
        )
        if not res.get("ok"):
            continue
        task_id = res["task_id"]
        # Tag the row so it's filterable in the UI as automation-driven.
        await db.cto_tasks.update_one(
            {"task_id": task_id},
            {"$set": {"source": "automation_webhook",
                      "automation_id": str(rule["_id"])}},
        )
        triggered.append(task_id)
        await db.cto_automations.update_one(
            {"_id": rule["_id"]},
            {"$set": {"last_triggered": time.time()},
             "$inc": {"trigger_count": 1}},
        )

    return {"ok": True, "triggered": len(triggered), "task_ids": triggered}


@router.post("/create")
async def create_automation(
    body: dict, authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    required = ("name", "repo_full_name", "trigger", "task_template")
    for f in required:
        if not body.get(f):
            raise HTTPException(400, f"{f} required")
    if body["trigger"] not in ("push", "cron", "manual"):
        raise HTTPException(400, "trigger must be push|cron|manual")
    db = require_db()
    doc = {
        "user_id":        me["user_id"],
        "name":           body["name"][:80],
        "repo_full_name": body["repo_full_name"].strip().strip("/"),
        "trigger":        body["trigger"],
        "branch_filter":  body.get("branch_filter") or None,
        "task_template":  body["task_template"],
        "cron_schedule":  body.get("cron_schedule") or None,
        "enabled":        True,
        "created_at":     time.time(),
        "trigger_count":  0,
        "last_triggered": None,
    }
    r = await db.cto_automations.insert_one(doc)
    return {"ok": True, "automation_id": str(r.inserted_id)}


@router.get("/list")
async def list_automations(
    authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    rows = await db.cto_automations.find(
        {"user_id": me["user_id"]},
    ).sort("created_at", -1).to_list(50)
    for r in rows:
        r["_id"] = str(r["_id"])
    return {"ok": True, "automations": rows}


@router.post("/{automation_id}/run")
async def run_automation_now(
    automation_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    """Manually fire an automation against its repo.

    Useful for cron-style rules that the user wants to trigger ahead of
    schedule, or for `manual` triggers (the only way they ever run).
    """
    me = await current_dev(authorization)
    db = require_db()
    rule = await db.cto_automations.find_one(
        {"_id": ObjectId(automation_id), "user_id": me["user_id"]},
    )
    if not rule:
        raise HTTPException(404, "Automation not found")

    repo_full = rule.get("repo_full_name", "")
    owner, _, repo = repo_full.partition("/")
    proj = await db.cto_projects.find_one({
        "user_id":      me["user_id"],
        "github_owner": owner,
        "github_repo":  repo,
    })
    if not proj:
        raise HTTPException(409, "No project matches this automation's repo")

    try:
        description = rule["task_template"].format(
            branch=rule.get("branch_filter") or "main",
            pusher=me.get("email") or me["user_id"],
            repo=repo_full, commit_count=0, commit_messages="(manual run)",
        )
    except KeyError:
        description = rule["task_template"]

    from routers.cto_projects import _enqueue_cto_task
    res = await _enqueue_cto_task(
        user_id=me["user_id"],
        project_id=proj.get("project_id"),
        task_text=description,
    )
    if not res.get("ok"):
        raise HTTPException(409, res.get("reason", "could not enqueue"))

    task_id = res["task_id"]
    await db.cto_tasks.update_one(
        {"task_id": task_id},
        {"$set": {"source": "automation_manual",
                  "automation_id": str(rule["_id"])}},
    )
    await db.cto_automations.update_one(
        {"_id": rule["_id"]},
        {"$set": {"last_triggered": time.time()},
         "$inc": {"trigger_count": 1}},
    )
    return {"ok": True, "task_id": task_id}


@router.post("/{automation_id}/toggle")
async def toggle_automation(
    automation_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    row = await db.cto_automations.find_one(
        {"_id": ObjectId(automation_id), "user_id": me["user_id"]},
    )
    if not row:
        raise HTTPException(404, "Automation not found")
    new_state = not row.get("enabled", True)
    await db.cto_automations.update_one(
        {"_id": row["_id"]}, {"$set": {"enabled": new_state}},
    )
    return {"ok": True, "enabled": new_state}


@router.delete("/{automation_id}")
async def delete_automation(
    automation_id: str, authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    r = await db.cto_automations.delete_one(
        {"_id": ObjectId(automation_id), "user_id": me["user_id"]},
    )
    if r.deleted_count == 0:
        raise HTTPException(404, "Automation not found")
    return {"ok": True}
