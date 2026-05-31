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
from services.usage import get_usage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


async def _require_admin(authorization: Optional[str]) -> dict:
    user = await current_dev(authorization)
    if user.get("is_admin"):
        return user
    # Stale-JWT escape hatch: the JWT might be from before the user was
    # promoted (e.g. founder allow-list added after their last login).
    # Trust the live DB row over the cached claim.
    db = get_db()
    if db is not None:
        row = await db.dev_users.find_one(
            {"user_id": user.get("user_id")},
            {"is_admin": 1, "tier": 1, "email": 1, "is_unlimited": 1},
        )
        if row and (row.get("is_admin") or row.get("tier") == "founder"):
            user["is_admin"] = True
            user["tier"] = row.get("tier") or user.get("tier")
            user["is_unlimited"] = bool(row.get("is_unlimited"))
            user["email"] = row.get("email") or user.get("email")
            return user
    raise HTTPException(403, "Admin access required")


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
    # Live token budget (plan + admin-granted bonus - used)
    try:
        user["usage"] = await get_usage(user_id)
    except Exception as e:
        logger.warning(f"usage lookup failed for {user_id}: {e}")
        user["usage"] = None
    # Recent admin grants for this user
    user["token_grants"] = await db.cto_token_grants.find(
        {"user_id": user_id}, {"_id": 0},
    ).sort("granted_at", -1).limit(20).to_list(20)
    return user


class GrantTokensBody(BaseModel):
    tokens: int
    reason: str = ""


@router.post("/users/{user_id}/grant-tokens")
async def grant_tokens(
    user_id: str,
    body: GrantTokensBody,
    authorization: Optional[str] = Header(None),
):
    """Admin manually credits a user with bonus tokens.

    Bonus tokens are tracked SEPARATELY on `dev_users.tokens_granted` and added
    on top of the plan limit (see `services.usage.get_usage`). Every grant is
    appended to `cto_token_grants` for audit.
    """
    admin = await _require_admin(authorization)
    db = require_db()
    if not isinstance(body.tokens, int) or body.tokens <= 0:
        raise HTTPException(400, "tokens must be a positive integer")
    if body.tokens > 10_000_000:
        raise HTTPException(400, "tokens grant too large (max 10M)")
    target = await db.dev_users.find_one({"user_id": user_id}, {"user_id": 1})
    if not target:
        raise HTTPException(404, "User not found")

    now = time.time()
    await db.dev_users.update_one(
        {"user_id": user_id},
        {"$inc": {"tokens_granted": body.tokens}, "$set": {"updated_at": now}},
    )
    await db.cto_token_grants.insert_one({
        "user_id": user_id,
        "tokens": body.tokens,
        "reason": (body.reason or "").strip()[:500],
        "granted_by": admin.get("email") or admin.get("user_id"),
        "granted_at": now,
    })
    usage = await get_usage(user_id)
    return {"ok": True, "granted": body.tokens, "usage": usage}


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

    # Real token usage from done tasks (Iter 25 — token tracking)
    pipe = [
        {"$match": {"created_at": {"$gte": month_ago}, "status": "done"}},
        {"$group": {"_id": "$agent_used", "tokens": {"$sum": "$tokens_used"}}},
    ]
    month_by_agent = {}
    async for d in db.cto_tasks.aggregate(pipe):
        month_by_agent[d.get("_id") or "deepseek"] = d.get("tokens") or 0

    day_pipe = [
        {"$match": {"created_at": {"$gte": day_ago}, "status": "done"}},
        {"$group": {"_id": "$agent_used", "tokens": {"$sum": "$tokens_used"}}},
    ]
    day_by_agent = {}
    async for d in db.cto_tasks.aggregate(day_pipe):
        day_by_agent[d.get("_id") or "deepseek"] = d.get("tokens") or 0

    # Cost per 1k tokens — DeepSeek via OpenRouter ~ $0.30 average
    cost_per_1k = {"deepseek": 0.30, "maxx": 0.65, "groq": 0.03}
    def calc(agent_map):
        return round(sum(
            (t / 1000) * cost_per_1k.get(a, 0.30)
            for a, t in agent_map.items()
        ), 2)

    ai_cost_month = calc(month_by_agent)
    ai_cost_today = calc(day_by_agent)

    done_month = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": month_ago}, "status": "done"}
    )
    done_today = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": day_ago}, "status": "done"}
    )
    chat_month = await db.chat_sessions.count_documents(
        {"updated_at": {"$gte": month_ago}}
    )

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
        "month_by_agent": month_by_agent,
        "day_by_agent": day_by_agent,
        "stripe_configured": False,
        "_note": (
            "Real token usage from completed tasks. Cost rates: "
            "DeepSeek $0.30, Maxx $0.65, Groq $0.03 per 1k tokens."
        ),
    }


# ── Empty stubs for unbuilt features ──────────────────────────────────
@router.get("/payments")
async def list_payments(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    payments = await db.cto_payments.find(
        {}, {"_id": 0},
    ).sort("created_at", -1).limit(100).to_list(100)
    total_revenue = round(sum(
        p.get("amount", 0) for p in payments
        if p.get("payment_status") == "paid"
    ), 2)
    return {
        "payments": payments,
        "total_revenue": total_revenue,
        "count": len(payments),
    }


@router.get("/support")
async def list_support_tickets(
    status: str = "",
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    q: dict = {}
    if status:
        q["status"] = status
    tickets = await db.cto_support.find(q, {"_id": 0}).sort(
        "updated_at", -1
    ).limit(100).to_list(100)
    for t in tickets:
        t["messages"] = await db.cto_support_messages.find(
            {"ticket_id": t.get("ticket_id")}, {"_id": 0},
        ).sort("ts", 1).to_list(200)
    return {"tickets": tickets}


class SupportReply(BaseModel):
    message: str


@router.post("/support/{ticket_id}/reply")
async def admin_reply(
    ticket_id: str,
    body: SupportReply,
    authorization: Optional[str] = Header(None),
):
    user = await _require_admin(authorization)
    db = require_db()
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(400, "Empty message")
    now = time.time()
    await db.cto_support_messages.insert_one({
        "ticket_id": ticket_id,
        "sender": "admin",
        "admin_email": user.get("email"),
        "message": msg,
        "ts": now,
    })
    r = await db.cto_support.update_one(
        {"ticket_id": ticket_id},
        {"$set": {"status": "pending_user", "updated_at": now, "last_reply_at": now}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Ticket not found")
    return {"ok": True}


@router.post("/support/{ticket_id}/resolve")
async def admin_resolve(
    ticket_id: str,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    r = await db.cto_support.update_one(
        {"ticket_id": ticket_id},
        {"$set": {"status": "resolved", "resolved_at": time.time(),
                  "updated_at": time.time()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Ticket not found")
    return {"ok": True, "status": "resolved"}


# ── Daily digest ──────────────────────────────────────────────────────
@router.get("/digest")
async def get_digest(authorization: Optional[str] = Header(None)):
    """Returns the same 1-pager that the daily cron sends. Preview-friendly."""
    await _require_admin(authorization)
    from services.daily_digest import build_digest
    return await build_digest()


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
