"""
routers/admin.py — Admin panel endpoints.

All routes require a JWT with `is_admin: true`. The admin user is whoever
matches the email in env `ADMIN_EMAIL`; on login the existing auth router
sets `is_admin=true` for that user.

Mounted under /api/aurem-dev/admin/* by main.py.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db, require_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


async def _require_admin(authorization: Optional[str]) -> dict:
    user = await current_dev(authorization)
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin access required")
    return user


# ── Auth check ──────────────────────────────────────────────────────────
@router.get("/me")
async def admin_me(authorization: Optional[str] = Header(None)):
    user = await _require_admin(authorization)
    return {"email": user.get("email"), "user_id": user.get("user_id"),
            "is_admin": True}


# ── Dashboard ──────────────────────────────────────────────────────────
@router.get("/dashboard")
async def dashboard(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    day_ago = now - 86400

    total_users = await db.dev_users.count_documents({})
    total_tasks = await db.cto_tasks.count_documents({})
    tasks_today = await db.cto_tasks.count_documents({"created_at": {"$gte": day_ago}})
    failed_tasks = await db.cto_tasks.count_documents({"status": "failed"})
    done_tasks = await db.cto_tasks.count_documents({"status": "done"})
    total_projects = await db.cto_projects.count_documents({})
    total_sessions = await db.chat_sessions.count_documents({})

    recent_tasks = await db.cto_tasks.find(
        {}, {"_id": 0, "steps": 0, "rollback_steps": 0}
    ).sort("created_at", -1).limit(5).to_list(5)

    recent_users = await db.dev_users.find(
        {}, {"_id": 0, "password_hash": 0, "github.access_token": 0}
    ).sort("created_at", -1).limit(5).to_list(5)

    return {
        "total_users": total_users,
        "total_tasks": total_tasks,
        "tasks_today": tasks_today,
        "failed_tasks": failed_tasks,
        "done_tasks": done_tasks,
        "success_rate": round((done_tasks / max(total_tasks, 1)) * 100, 1),
        "total_projects": total_projects,
        "total_sessions": total_sessions,
        "recent_tasks": recent_tasks,
        "recent_users": recent_users,
    }


# ── Users ──────────────────────────────────────────────────────────
@router.get("/users")
async def list_users(
    search: str = "",
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    query: dict = {}
    if search:
        query = {"$or": [
            {"email": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
        ]}
    users = await db.dev_users.find(
        query, {"_id": 0, "password_hash": 0, "github.access_token": 0}
    ).sort("created_at", -1).limit(100).to_list(100)

    for u in users:
        uid = u.get("user_id", "")
        u["project_count"] = await db.cto_projects.count_documents({"user_id": uid})
        u["task_count"] = await db.cto_tasks.count_documents({"user_id": uid})
        u["session_count"] = await db.chat_sessions.count_documents({"user_id": uid})
    return {"users": users}


@router.get("/users/{user_id}")
async def get_user(user_id: str, authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    user = await db.dev_users.find_one(
        {"user_id": user_id},
        {"_id": 0, "password_hash": 0, "github.access_token": 0},
    )
    if not user:
        raise HTTPException(404, "User not found")
    user["projects"] = await db.cto_projects.find(
        {"user_id": user_id},
        {"_id": 0, "github_token": 0},
    ).to_list(50)
    user["recent_tasks"] = await db.cto_tasks.find(
        {"user_id": user_id},
        {"_id": 0, "steps": 0, "rollback_steps": 0},
    ).sort("created_at", -1).limit(20).to_list(20)
    user["project_count"] = len(user["projects"])
    user["task_count"] = await db.cto_tasks.count_documents({"user_id": user_id})
    user["session_count"] = await db.chat_sessions.count_documents({"user_id": user_id})
    return user


class SuspendBody(BaseModel):
    suspend: bool


@router.post("/users/{user_id}/suspend")
async def toggle_suspend(
    user_id: str,
    body: SuspendBody,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    status = "suspended" if body.suspend else "active"
    r = await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {"status": status, "status_changed_at": time.time()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "User not found")
    return {"ok": True, "status": status}


# ── Projects ──────────────────────────────────────────────────────────
@router.get("/projects")
async def list_all_projects(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    projects = await db.cto_projects.find(
        {}, {"_id": 0, "github_token": 0},
    ).sort("created_at", -1).limit(200).to_list(200)
    return {"projects": projects, "total": len(projects)}


# ── Tasks ──────────────────────────────────────────────────────────
@router.get("/tasks")
async def list_all_tasks(
    status: str = "",
    limit: int = 50,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    query: dict = {}
    if status:
        query["status"] = status
    tasks = await db.cto_tasks.find(
        query, {"_id": 0, "steps": 0, "rollback_steps": 0},
    ).sort("created_at", -1).limit(limit).to_list(limit)
    return {"tasks": tasks, "total": len(tasks)}


# ── Token P&L (best-effort from existing data) ─────────────────────────
@router.get("/token-pnl")
async def token_pnl(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    day_ago = now - 86400
    month_ago = now - 86400 * 30

    # We don't track per-task token usage yet — use task counts as a proxy.
    done_month = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": month_ago}, "status": "done"}
    )
    done_today = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": day_ago}, "status": "done"}
    )
    chat_month = await db.chat_sessions.count_documents(
        {"updated_at": {"$gte": month_ago}}
    )

    # Rough cost estimate: $0.01/task + $0.005/chat session
    ai_cost_month = round(done_month * 0.01 + chat_month * 0.005, 2)
    ai_cost_today = round(done_today * 0.01, 2)

    return {
        "revenue_month": 0,
        "stripe_fees": 0,
        "net_revenue": 0,
        "ai_cost_month": ai_cost_month,
        "ai_cost_today": ai_cost_today,
        "net_profit": -ai_cost_month,
        "margin_pct": 0,
        "tasks_done_month": done_month,
        "tasks_done_today": done_today,
        "chat_sessions_month": chat_month,
        "stripe_configured": False,
        "_note": (
            "Stripe not configured yet — revenue is 0. Token tracking "
            "per task is on the P2 backlog; current AI cost is a "
            "task-count proxy ($0.01/task, $0.005/chat session)."
        ),
    }


# ── Empty stubs for unbuilt features ──────────────────────────────────
@router.get("/payments")
async def list_payments(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    return {
        "payments": [],
        "total_revenue": 0,
        "_note": "Stripe integration is on the P2 backlog. No payment data yet.",
    }


@router.get("/support")
async def list_support_tickets(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    return {
        "tickets": [],
        "_note": "Support inbox not yet built. Add a `cto_support` "
                 "collection + ticket UI to enable.",
    }


# ── Architecture ──────────────────────────────────────────────────────
@router.get("/architecture")
async def get_architecture(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    import os
    import httpx
    db = get_db()
    services: dict = {"MongoDB": {
        "status": "live" if db is not None else "down",
        "latency_ms": 0,
    }}
    for name, url in [
        ("GitHub API", "https://api.github.com"),
        ("OpenRouter", "https://openrouter.ai/api/v1/models"),
    ]:
        try:
            t0 = time.time()
            async with httpx.AsyncClient(timeout=4.0) as c:
                r = await c.get(url)
            services[name] = {
                "status": "live" if r.status_code < 500 else "degraded",
                "latency_ms": round((time.time() - t0) * 1000),
            }
        except Exception:
            services[name] = {"status": "unreachable", "latency_ms": 0}

    return {
        "services": services,
        "integrations": {
            "openrouter (deepseek)": bool(os.getenv("OPENROUTER_API_KEY")),
            "emergent_llm (maxx)": bool(os.getenv("EMERGENT_LLM_KEY")),
            "github_oauth": bool(os.getenv("GITHUB_OAUTH_CLIENT_ID")),
            "mongodb": db is not None,
            "stripe": bool(os.getenv("STRIPE_SECRET_KEY")),
        },
    }


# ── Settings ──────────────────────────────────────────────────────────
@router.get("/settings")
async def get_settings(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    doc = await db.cto_settings.find_one({"_id": "global"}, {"_id": 0})
    return doc or {
        "token_limits": {"free": 10000, "pro": 50000, "team": 100000},
        "pricing": {"free": 0, "pro": 29, "team": 99},
    }


@router.post("/settings")
async def save_settings(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    body = await request.json()
    body["updated_at"] = time.time()
    await db.cto_settings.update_one(
        {"_id": "global"}, {"$set": body}, upsert=True,
    )
    return {"ok": True}
