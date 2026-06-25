"""
routers/admin.py — Admin panel endpoints.

All routes require a JWT with `is_admin: true`. The admin user is whoever
matches the email in env `ADMIN_EMAIL`; on login the existing auth router
sets `is_admin=true` for that user.

Mounted under /api/aurem-dev/admin/* by main.py.
"""
from __future__ import annotations

import logging
import os
import asyncio
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


# ── Iter 210 — Audit feed (CitationGuard + ToolExecutor signals) ─────
@router.get("/audit")
async def audit_feed(
    limit:      int = 100,
    user_id:    Optional[str] = None,
    project_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    """Read the `ora_audit` collection. Backed by `audit_log.list_turns`.
    Used by the admin panel's Audit tab to surface every ORA turn,
    citation-guard triggers, and tool-error signals.
    """
    await _require_admin(authorization)
    from services.audit_log import list_turns
    rows = await list_turns(
        user_id=user_id,
        project_id=project_id,
        limit=max(1, min(int(limit or 100), 500)),
    )
    return {"ok": True, "rows": rows, "count": len(rows)}





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
        {}, {"_id": 0, "password": 0, "password_hash": 0, "github.access_token": 0}
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


@router.get("/council/stats")
async def council_stats(authorization: Optional[str] = Header(None)):
    """Lightweight aggregate for AdminOverview — total council rows +
    last-30-days slice + Claude correction rate + per-mode breakdown.
    No PII."""
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    cutoff = now - (30 * 86400)

    total = await db.ora_council_logs.count_documents({})
    last_30d = await db.ora_council_logs.count_documents(
        {"timestamp": {"$gte": cutoff}}
    )
    # Per-mode breakdown (used by AdminOverview metric grid).
    mode_a = await db.ora_council_logs.count_documents({"mode": "A"})
    mode_b = await db.ora_council_logs.count_documents({"mode": "B"})
    mode_c = await db.ora_council_logs.count_documents({"mode": "C"})
    mode_d = await db.ora_council_logs.count_documents({"mode": "D"})
    mode_e = await db.ora_council_logs.count_documents({"mode": "E"})
    mode_f = await db.ora_council_logs.count_documents({"mode": "F"})
    lint_blocked = await db.ora_council_logs.count_documents(
        {"lint_blocked": True}
    )
    corrected = await db.ora_council_logs.count_documents(
        {"mode": "C", "claude_corrected": True}
    )
    correction_rate = (
        round((corrected / mode_c) * 100, 1) if mode_c else 0.0
    )
    return {
        "total":           total,
        "total_logs":      total,        # AdminOverview reads this key too
        "last_30d":        last_30d,
        "mode_a":          mode_a,
        "mode_b":          mode_b,
        "mode_c":          mode_c,
        "mode_d":          mode_d,
        "mode_e":          mode_e,
        "mode_f":          mode_f,
        "code_rows":       mode_c,
        "corrections":     corrected,
        "corrected":       corrected,
        "lint_blocked":    lint_blocked,
        "correction_rate": correction_rate,
        "ready_for_finetune": total >= 1000,
    }



# ── Users ──────────────────────────────────────────────────────────
@router.get("/ora-learning/weekly-summary")
async def ora_learning_weekly_summary(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 153 — top low-confidence patterns from the last 7 days of
    ora_learning_logs. Powers the AdminOverview learning card."""
    await _require_admin(authorization)
    db = require_db()
    cutoff = time.time() - 7 * 86400
    cur = db.ora_learning_logs.aggregate([
        {"$match": {"ts": {"$gte": cutoff}}},
        {"$group": {"_id": "$reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 10},
    ])
    rows = await cur.to_list(50)
    total = sum(r.get("n", 0) for r in rows)
    return {
        "ok": True,
        "window_days": 7,
        "total": total,
        "patterns": [{"reason": r["_id"], "count": r["n"]} for r in rows],
    }



@router.get("/users")
async def list_users(
    search: str = "",
    window: str = "all",
    authorization: Optional[str] = Header(None),
):
    """List users with optional search + signup-time window filter.

    `window` accepts: `24h`, `7d`, `30d`, `all`. Bucket counts for all
    three windows are returned in the same payload so the UI can render
    filter pills without an extra request.
    """
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    buckets = {
        "24h": now - 86_400,
        "7d":  now - 7 * 86_400,
        "30d": now - 30 * 86_400,
    }
    # Always compute the three bucket counts (cheap — one count_documents
    # each, all over an indexed `created_at`). These power the filter
    # pills in the admin UI.
    bucket_counts: dict[str, int] = {}
    for label, since in buckets.items():
        try:
            bucket_counts[label] = await db.dev_users.count_documents(
                {"created_at": {"$gte": since}}
            )
        except Exception as e:
            logger.warning("list_users bucket[%s] failed: %r", label, e)
            bucket_counts[label] = 0
    try:
        bucket_counts["all"] = await db.dev_users.count_documents({})
    except Exception:
        bucket_counts["all"] = 0

    query: dict = {}
    if search:
        query = {"$or": [
            {"email": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
        ]}
    if window in buckets:
        query["created_at"] = {"$gte": buckets[window]}

    users = await db.dev_users.find(
        query, {"_id": 0, "password": 0, "password_hash": 0, "github.access_token": 0}
    ).sort("created_at", -1).limit(100).to_list(100)

    # Iter 120 — flatten 300 round-trips (3 count_documents per user) into
    # 3 grouped aggregations. Critical for /admin/users responsiveness
    # under load; the old pattern was an N+1 hotspot flagged by the
    # deployment agent and a candidate cause for OOM/timeout on Atlas
    # free tier where connection slots are limited.
    uids = [u.get("user_id", "") for u in users]
    if uids:
        async def _counts(coll_name: str) -> dict:
            coll = getattr(db, coll_name)
            cur = coll.aggregate([
                {"$match": {"user_id": {"$in": uids}}},
                {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
            ])
            return {row["_id"]: row["n"] async for row in cur}
        proj_counts = await _counts("cto_projects")
        task_counts = await _counts("cto_tasks")
        sess_counts = await _counts("chat_sessions")
    else:
        proj_counts = task_counts = sess_counts = {}

    for u in users:
        uid = u.get("user_id", "")
        u["project_count"] = proj_counts.get(uid, 0)
        u["task_count"]    = task_counts.get(uid, 0)
        u["session_count"] = sess_counts.get(uid, 0)
    return {"users": users, "bucket_counts": bucket_counts}


@router.get("/users/{user_id}")
async def get_user(user_id: str, authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    user = await db.dev_users.find_one(
        {"user_id": user_id},
        {"_id": 0, "password": 0, "password_hash": 0, "github.access_token": 0},
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


# ── Iter 65 — Per-agent token consumption with range selector ──────────
# UI calls this with ?range=24h|7d|30d|90d|365d and renders a small
# comparison chart in the Users tab. Goal: Teji can answer "kya
# Claude/Maxx ka extra cost worth hai vs DeepSeek for the same task?"
@router.get("/agent-tokens")
async def agent_tokens(
    range: str = "7d",
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    await _require_admin(authorization)
    db = require_db()
    range_map = {
        "24h":   ("hourly",   86400,            3600),     # 24 hourly buckets
        "7d":    ("daily",    7 * 86400,        86400),    # 7 daily
        "30d":   ("daily",    30 * 86400,       86400),    # 30 daily
        "90d":   ("weekly",   90 * 86400,       7 * 86400),
        "365d":  ("monthly",  365 * 86400,      30 * 86400),
    }
    if range not in range_map:
        range = "7d"
    bucket_label, window_secs, bucket_secs = range_map[range]
    now = time.time()
    since = now - window_secs

    # Pull every done task in window with agent + tokens + created_at.
    cur = db.cto_tasks.find(
        {"created_at": {"$gte": since}, "status": "done"},
        {"agent_used": 1, "tokens_used": 1, "created_at": 1,
         "claude_corrected": 1, "_id": 0},
    )
    # Cost rates per 1k tokens (real OpenRouter/Anthropic prices Feb 2026)
    cost_per_1k = {"deepseek": 0.30, "maxx": 0.65, "claude": 0.65, "groq": 0.03}

    # Bucket → agent → tokens
    buckets: dict[float, dict[str, int]] = {}
    totals = {"deepseek": 0, "maxx": 0, "claude": 0, "groq": 0}
    task_counts = {"deepseek": 0, "maxx": 0, "claude": 0, "groq": 0}
    claude_corrections = 0

    async for d in cur:
        agent = (d.get("agent_used") or "deepseek").lower()
        if agent not in totals:
            agent = "deepseek"
        tk = int(d.get("tokens_used") or 0)
        bucket_start = (int(d.get("created_at", now) // bucket_secs)) * bucket_secs
        bkt = buckets.setdefault(bucket_start, {a: 0 for a in totals})
        bkt[agent] += tk
        totals[agent] += tk
        task_counts[agent] += 1
        if d.get("claude_corrected"):
            claude_corrections += 1

    # Format chronological series
    series = []
    for bs in sorted(buckets.keys()):
        row = {"ts": int(bs), "label": _bucket_label(bs, bucket_label)}
        for agent, val in buckets[bs].items():
            row[agent] = val
        series.append(row)

    # Cost summary
    costs = {a: round((t / 1000) * cost_per_1k.get(a, 0.30), 4)
             for a, t in totals.items()}
    total_cost = round(sum(costs.values()), 4)

    # Per-task averages — the key question Teji asks
    avg_per_task = {}
    for a in totals:
        n = task_counts[a]
        avg_per_task[a] = {
            "tokens_avg": round(totals[a] / n) if n else 0,
            "cost_avg_usd": round((totals[a] / n / 1000) * cost_per_1k.get(a, 0.30), 4)
                             if n else 0,
        }

    # Claude-vs-DeepSeek delta — directly answers the "how much extra"
    extra_for_claude = None
    if task_counts["deepseek"] > 0 and (task_counts["maxx"] + task_counts["claude"]) > 0:
        ds_avg_cost = avg_per_task["deepseek"]["cost_avg_usd"]
        maxx_avg_tokens = (
            totals["maxx"] + totals["claude"]
        ) / max(task_counts["maxx"] + task_counts["claude"], 1)
        claude_rate = cost_per_1k["maxx"]
        claude_avg_cost = round((maxx_avg_tokens / 1000) * claude_rate, 4)
        extra_for_claude = {
            "deepseek_avg_cost_per_task": ds_avg_cost,
            "claude_maxx_avg_cost_per_task": claude_avg_cost,
            "delta_usd_per_task": round(claude_avg_cost - ds_avg_cost, 4),
            "delta_multiplier": round(claude_avg_cost / ds_avg_cost, 2)
                                if ds_avg_cost else None,
        }

    return {
        "range": range,
        "bucket": bucket_label,
        "buckets_count": len(series),
        "series": series,
        "totals_tokens": totals,
        "task_counts": task_counts,
        "claude_corrections": claude_corrections,
        "cost_per_1k_usd": cost_per_1k,
        "costs_usd": costs,
        "total_cost_usd": total_cost,
        "avg_per_task": avg_per_task,
        "claude_vs_deepseek": extra_for_claude,
    }


def _bucket_label(ts: float, granularity: str) -> str:
    """Human-readable bucket label for the chart x-axis."""
    from datetime import datetime, timezone as _tz
    dt = datetime.fromtimestamp(ts, tz=_tz.utc)
    if granularity == "hourly":
        return dt.strftime("%H:00")
    if granularity == "daily":
        return dt.strftime("%b %d")
    if granularity == "weekly":
        return f"wk {dt.strftime('%b %d')}"
    return dt.strftime("%b %Y")



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
    import asyncio
    import httpx
    from services.external_services_registry import (
        REGISTRY, is_configured, should_probe,
    )
    db = get_db()
    services: dict = {"MongoDB": {
        "status": "live" if db is not None else "down",
        "latency_ms": 0,
    }}
    # Iter 124 — PARALLEL probes (was sequential — worst case 8 svcs × 4s = 32s
    # which is enough to trip Cloudflare 524 under cold-start CPU contention).
    # Now total wall-clock = slowest single probe ≈ 4s cap.
    probe_targets = [svc for svc in REGISTRY if should_probe(svc)]

    async def _probe_one(svc):
        try:
            t0 = time.time()
            # Per-call client so a hung connect doesn't share state with peers.
            async with httpx.AsyncClient(timeout=4.0) as c:
                r = await c.get(
                    svc.probe_url,
                    headers={"User-Agent": "AUREM-arch-probe/1.0"},
                )
            elapsed_ms = round((time.time() - t0) * 1000)
            if r.status_code < 500:
                return svc.display_name, {
                    "status": "live", "latency_ms": elapsed_ms,
                }
            return svc.display_name, {
                "status": "degraded", "latency_ms": elapsed_ms,
                "note": f"HTTP {r.status_code}",
            }
        except Exception as e:
            return svc.display_name, {
                "status": "unreachable", "latency_ms": 0,
                "note": str(e)[:80],
            }

    if probe_targets:
        # asyncio.gather with timeout guard — even if every probe somehow
        # exceeds its own 4s budget, we never let the whole endpoint hang.
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(_probe_one(s) for s in probe_targets),
                               return_exceptions=False),
                timeout=8.0,
            )
            for name, info in results:
                services[name] = info
        except asyncio.TimeoutError:
            # Mark anything missing as "unreachable" — never crash the page.
            for svc in probe_targets:
                services.setdefault(svc.display_name, {
                    "status": "unreachable", "latency_ms": 0,
                    "note": "probe timed out",
                })

    # Iter 123f — integrations grid is also generated from the registry.
    # `mongodb` is special-cased because there's no env key for it (the
    # db handle itself is the truth).
    integrations: dict[str, bool] = {"mongodb": db is not None}
    for svc in REGISTRY:
        integrations[svc.integration_id] = is_configured(svc)

    missing = [k for k, v in integrations.items() if not v]
    note = (
        f"{sum(integrations.values())}/{len(integrations)} integrations configured."
        + (f" Missing: {', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}."
           if missing else " All systems wired.")
    )
    return {
        "services": services,
        "integrations": integrations,
        "note": note,
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


# ── Iter 40 — ORA Council (Two-Agent Maxx telemetry) ───────────────────
@router.get("/ora/stats")
async def ora_council_stats(authorization: Optional[str] = Header(None)):
    """Quick summary: total logs, by-mode counts, correction rate,
    pending-export queue, fine-tune readiness."""
    await _require_admin(authorization)
    from services.ora_council_logger import get_council_stats
    return await get_council_stats(require_db())


@router.get("/ora-stats")
async def ora_council_stats_v2(authorization: Optional[str] = Header(None)):
    """Alias for AuremAdminPanel (uses /admin/ora-stats path)."""
    await _require_admin(authorization)
    from services.ora_council_logger import get_council_stats
    return await get_council_stats(require_db())


@router.post("/ora/export")
async def ora_council_export(authorization: Optional[str] = Header(None)):
    """Manually trigger yesterday's JSONL export. Daily cron also runs it."""
    await _require_admin(authorization)
    from services.ora_council_logger import export_daily_jsonl
    return await export_daily_jsonl(require_db())


# ── Iter 41 — Project Brain (per-repo persistent memory) ───────────────
@router.get("/project-brain/{project_id}")
async def admin_project_brain(
    project_id: str,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.project_brain import get_brain_full
    brain = await get_brain_full(require_db(), project_id)
    return brain or {"project_id": project_id, "empty": True}


class BrainDecisionBody(BaseModel):
    title: str
    reason: str


@router.post("/project-brain/{project_id}/decision")
async def admin_brain_add_decision(
    project_id: str,
    body: BrainDecisionBody,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.project_brain import add_decision
    await add_decision(require_db(), project_id, body.title, body.reason)
    return {"ok": True}


class BrainPreferenceBody(BaseModel):
    preference: str


@router.post("/project-brain/{project_id}/preference")
async def admin_brain_add_preference(
    project_id: str,
    body: BrainPreferenceBody,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.project_brain import add_preference
    await add_preference(require_db(), project_id, body.preference)
    return {"ok": True}



# ── Iter 47 — Brain inline-delete endpoints ──
@router.delete("/project-brain/{project_id}/decision")
async def admin_brain_delete_decision(
    project_id: str,
    title: str,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.project_brain import delete_decision
    n = await delete_decision(require_db(), project_id, title)
    return {"ok": True, "removed": n}


@router.get("/brain/{project_id}/dump")
async def admin_brain_dump(
    project_id: str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Returns exactly what ORA sees for this project.

    Used when "ORA gave wrong answer" — founder can compare the user's
    question against the literal context block that was injected into
    the system prompt. Includes raw brain document for decision/pref
    inline deletion + the assembled string for diff debugging.
    """
    await _require_admin(authorization)
    db = require_db()
    proj = await db.cto_projects.find_one({"project_id": project_id})
    if not proj:
        raise HTTPException(404, "Project not found")

    # Fetch the PAT so the assembled context includes remote commits
    # (matches what ORA would see for this user in a real chat turn).
    token = None
    try:
        from routers.cto_projects import _decrypt_pat, _user_gh_token
        token = await _decrypt_pat(proj["user_id"], proj.get("github_token")) \
            or await _user_gh_token(proj["user_id"])
    except Exception:
        token = None

    brain_doc = await db.project_brains.find_one({"project_id": project_id}) or {}
    # Strip Mongo _id from the raw doc so it stays JSON-serialisable
    brain_doc.pop("_id", None)

    from services.project_brain import get_brain_context
    repo_full = f"{proj.get('github_owner', '')}/{proj.get('github_repo', '')}"
    try:
        assembled = await get_brain_context(
            db, project_id, repo_full, github_token=token,
        )
    except Exception as e:
        assembled = f"(error assembling context: {e})"

    return {
        "project_id":           project_id,
        "repo":                 repo_full,
        "raw_brain":            brain_doc,
        "assembled_context":    assembled,
        "context_length_chars": len(assembled),
        "has_github_commits":   "Recent GitHub commits" in assembled,
        "has_aurem_commits":    "Recent commits AUREM" in assembled,
        "has_decisions":        bool(brain_doc.get("decisions")),
        "has_preferences":      bool(brain_doc.get("team_preferences")
                                     or brain_doc.get("preferences")),
        "had_pat":              bool(token),
    }


# ── Code surface (live file map for /admin/architecture) ─────────────
@router.get("/code-surface")
async def code_surface(authorization: Optional[str] = Header(None, alias="Authorization")):
    """Walk load-bearing source dirs and return live counts.

    Drift-proof replacement for the hand-maintained CODE_SURFACE constant
    on the Architecture page — the frontend reads from here so a new
    file in routers/ or pages/ shows up immediately."""
    await _require_admin(authorization)
    import os
    base = "/app"
    scan = {
        "routers":    "backend/routers",
        "services":   "backend/services",
        "pages":      "frontend/src/pages",
        "components": "frontend/src/components",
    }
    surface: dict[str, list[dict]] = {k: [] for k in scan}
    for category, rel in scan.items():
        full = os.path.join(base, rel)
        if not os.path.isdir(full):
            continue
        for fname in sorted(os.listdir(full)):
            if fname.startswith((".", "_")):
                continue
            if not fname.endswith((".py", ".jsx", ".tsx", ".js", ".ts")):
                continue
            fpath = os.path.join(full, fname)
            try:
                with open(fpath, encoding="utf-8") as fh:
                    content = fh.read()
            except Exception:
                continue
            lines = content.count("\n")
            desc = ""
            for raw in content.splitlines()[:10]:
                t = raw.strip()
                if not t:
                    continue
                if t.startswith(('"""', "'''")):
                    desc = t.strip("\"'").strip()
                    break
                if t.startswith("/*") or t.startswith("//") or t.startswith("*"):
                    desc = t.lstrip("/*").lstrip("/ *").strip()
                    break
                if (t.startswith("import") or t.startswith("from")
                        or t.startswith("<") or t.startswith("{")):
                    continue
                if t.startswith("#") and not t.startswith("#!"):
                    desc = t.lstrip("# ").strip()
                    break
            surface[category].append({
                "file":  fname,
                "lines": lines,
                "desc":  desc[:80],
                "path":  os.path.join(rel, fname),
            })
    return {
        "ok":          True,
        "surface":     surface,
        "total_files": sum(len(v) for v in surface.values()),
    }


# ── Web skill smoke endpoints (Iter 79) ───────────────────────────────
# Direct REST entry points so devs (and pytest) can hit Tavily/Firecrawl
# without going through the LLM tool-call loop. Mounted at
# /api/aurem-dev/admin/skills/*. Admin-only — these calls cost money.

class _SkillBody(BaseModel):
    query: Optional[str] = None
    url: Optional[str] = None
    urls: Optional[list[str]] = None
    max_results: Optional[int] = None
    deep: Optional[bool] = None
    topic: Optional[str] = None
    formats: Optional[list[str]] = None
    limit: Optional[int] = None


async def _run_skill(name: str, body: _SkillBody, authorization: Optional[str]):
    await _require_admin(authorization)
    from services.web_skills import invoke_web_tool
    args = {k: v for k, v in body.model_dump().items() if v is not None}
    res = await invoke_web_tool(name, args, {})
    if res is None:
        raise HTTPException(404, f"Unknown skill: {name}")
    return res


@router.post("/skills/web-search")
async def skill_web_search(body: _SkillBody,
                           authorization: Optional[str] = Header(None)):
    return await _run_skill("web_search", body, authorization)


@router.post("/skills/fetch-url")
async def skill_fetch_url(body: _SkillBody,
                          authorization: Optional[str] = Header(None)):
    return await _run_skill("fetch_url", body, authorization)


@router.post("/skills/search-and-summarize")
async def skill_web_search_and_summarize(
    body: _SkillBody, authorization: Optional[str] = Header(None),
):
    return await _run_skill("web_search_and_summarize", body, authorization)


@router.post("/skills/firecrawl-scrape")
async def skill_firecrawl_scrape(body: _SkillBody,
                                 authorization: Optional[str] = Header(None)):
    return await _run_skill("firecrawl_scrape", body, authorization)


@router.post("/skills/firecrawl-crawl")
async def skill_firecrawl_crawl(body: _SkillBody,
                                authorization: Optional[str] = Header(None)):
    return await _run_skill("firecrawl_crawl_site", body, authorization)


@router.get("/skills/status")
async def skill_status(authorization: Optional[str] = Header(None)):
    """Reveal which web skills are wired (key present). No secrets returned."""
    await _require_admin(authorization)
    import os
    return {
        "ok": True,
        "skills": {
            "web_search":               bool(os.environ.get("TAVILY_API_KEY")),
            "fetch_url":                bool(os.environ.get("TAVILY_API_KEY")),
            "web_search_and_summarize": bool(os.environ.get("TAVILY_API_KEY")),
            "firecrawl_scrape":         bool(os.environ.get("FIRECRAWL_API_KEY")),
            "firecrawl_crawl_site":     bool(os.environ.get("FIRECRAWL_API_KEY")),
        },
    }


# ── Persona Quality Score (Iter 124g) ────────────────────────────────
# Surfaces the eval-as-CI history so the admin tile + customers (later
# via a public trust-badge) see real ORA quality over time.
@router.get("/eval-quality")
async def eval_quality(authorization: Optional[str] = Header(None)):
    """Last 30 days of eval runs (from `ora_eval_runs`). Returns:
       latest      — most recent run summary (None if no runs yet)
       trend       — chronological score list [{ts, score, hard_fails}]
       totals      — { runs, hard_fail_runs, avg_score, last30d_runs }
    Read-only, admin only, no external calls."""
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        return {"latest": None, "trend": [], "totals": {"runs": 0}}
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    docs = await db.ora_eval_runs.find(
        {"ts": {"$gte": cutoff}}, {"_id": 0},
    ).sort("ts", 1).to_list(length=200)
    trend = [{
        "ts":         d.get("ts"),
        "score":      round(100 * (d.get("passed", 0) / max(d.get("total", 1), 1))),
        "hard_fails": d.get("hard_fails", 0),
        "ok":         bool(d.get("ok")),
    } for d in docs]
    latest = docs[-1] if docs else None
    avg_score = round(sum(t["score"] for t in trend) / len(trend)) if trend else None
    return {
        "latest": latest,
        "trend":  trend,
        "totals": {
            "runs":           len(docs),
            "hard_fail_runs": sum(1 for d in docs if d.get("hard_fails", 0) > 0),
            "avg_score":      avg_score,
            "last30d_runs":   len(docs),
        },
    }




# ── Architecture health report (Iter 86) ──────────────────────────────
# Surfaces the static-analysis health signal in /admin/architecture so
# the next 1952-line file is caught at 320, not 2000. Read-only, admin
# only, no LLM, no network — pure AST + filesystem walk via radon.

@router.get("/architecture-health")
async def architecture_health(
    summary: bool = False,
    authorization: Optional[str] = Header(None),
):
    """Run the architecture health report.

    Query params:
        summary=true → return a short text body instead of full JSON
                       (useful for one-line Admin tab headlines).
    """
    await _require_admin(authorization)
    from services.architecture_health import (
        run_health_report, summarise,
    )
    report = run_health_report()
    if summary:
        return {"ok": True, "summary": summarise(report),
                "counts": {
                    "bloated":     len(report["bloated_files"]),
                    "complex":     len(report["complexity_hits"]),
                    "circular":    len(report["circular_imports"]),
                    "violations":  len(report["boundary_violations"]),
                }}
    return {"ok": True, "report": report}


# ── Recent commits with SHAs (powers BrainDump "Show diff →" buttons) ─
@router.get("/brain/{project_id}/recent-commits")
async def brain_recent_commits(
    project_id: str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Return the last N (default 12) commit events from a project brain.

    Each row carries the SHA, one-line description, files touched, and
    a UTC timestamp. The BrainDump page renders these as a list with
    "Show diff →" buttons that dispatch `ora:prefill` so the user lands
    in chat with `get_commit_diff(<sha>)` pre-filled.
    """
    await _require_admin(authorization)
    db = require_db()
    brain_doc = await db["project_brains"].find_one(
        {"project_id": project_id},
        {"event_log": 1, "_id": 0},
    )
    if not brain_doc:
        return {"project_id": project_id, "commits": []}

    events = brain_doc.get("event_log") or []
    commits = [e for e in events if e.get("type") == "commit"]
    # Newest first; cap at 12 rows to keep the UI tight.
    commits = list(reversed(commits))[:12]

    rows = []
    for ev in commits:
        ts = ev.get("ts")
        rows.append({
            "sha":               (ev.get("sha") or "")[:40],
            "short_sha":         (ev.get("sha") or "")[:7],
            "description":       (ev.get("description") or "").strip().splitlines()[0][:160],
            "files":             ev.get("files") or [],
            "correction_applied": bool(ev.get("correction_applied")),
            "ts":                ts.isoformat() if hasattr(ts, "isoformat") else ts,
        })
    return {"project_id": project_id, "commits": rows}


# ── Mode classifier telemetry — rolling-window 100 docs ───────────────
@router.get("/mode-telemetry")
async def mode_telemetry(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Aggregates the last 100 mode classifications + the most recent 10
    raw entries. Lets the founder see which modes ORA picks most often,
    how often it asks for confirmation, and how confident it is on avg.
    """
    await _require_admin(authorization)
    db = require_db()
    docs = await db["mode_classifications"].find(
        {}, {"_id": 0}
    ).sort("ts", -1).limit(100).to_list(100)

    from collections import Counter
    mode_counts = Counter(d.get("mode", "?") for d in docs)
    needs_confirm_count = sum(1 for d in docs if d.get("needs_confirm"))
    f12_forced_count = sum(1 for d in docs if d.get("f12_forced"))
    avg_confidence = (
        sum(d.get("confidence", 0.0) for d in docs) / len(docs)
        if docs else 0.0
    )
    total = len(docs)
    return {
        "total":              total,
        "mode_counts":        dict(mode_counts),
        "needs_confirm_pct":  round(needs_confirm_count / max(total, 1) * 100, 1),
        "f12_forced_pct":     round(f12_forced_count    / max(total, 1) * 100, 1),
        "avg_confidence":     round(avg_confidence, 2),
        "recent":             docs[:10],
    }


# ── Product analytics — DAU/WAU/MAU, mode usage, task success, token burn ────
@router.get("/product-analytics")
async def product_analytics(
    days: int = 30,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Real product analytics — DAU, MAU, mode usage, feature adoption,
    task success rate, token burn, top users.

    Iter 139 — replaces the previous "Analytics" page that only showed
    Mode-Council debate logs. This endpoint hits the same collections
    the rest of the platform writes to, so every number is sourced
    from real user activity (no estimates, no stubs).
    """
    await _require_admin(authorization)
    db = require_db()
    import time
    from datetime import datetime, timezone

    # Iter 139 — clamp window so a malicious client can't probe with
    # days=10**12 and DoS the aggregation pipeline.
    days = max(1, min(int(days or 30), 365))
    now = time.time()
    window_start = now - (days * 86400)
    day_ago = now - 86400
    week_ago = now - (7 * 86400)

    async def _first_or_zero(coll, pipe: list, field: str) -> int:
        async for r in coll.aggregate(pipe):
            return int(r.get(field, 0) or 0)
        return 0

    # DAU — unique users who sent a chat message today
    dau = await _first_or_zero(
        db.chat_sessions,
        [
            {"$match": {"updated_at": {"$gte": day_ago}}},
            {"$group": {"_id": "$user_id"}},
            {"$count": "dau"},
        ],
        "dau",
    )

    # WAU — unique users active this week
    wau = await _first_or_zero(
        db.chat_sessions,
        [
            {"$match": {"updated_at": {"$gte": week_ago}}},
            {"$group": {"_id": "$user_id"}},
            {"$count": "wau"},
        ],
        "wau",
    )

    # MAU — unique users active this month
    mau = await _first_or_zero(
        db.chat_sessions,
        [
            {"$match": {"updated_at": {"$gte": window_start}}},
            {"$group": {"_id": "$user_id"}},
            {"$count": "mau"},
        ],
        "mau",
    )

    # Total users
    total_users = await db.dev_users.estimated_document_count()

    # New users this week
    new_users_week = await db.dev_users.count_documents(
        {"created_at": {"$gte": week_ago}}
    )

    # Task stats
    tasks_total = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": window_start}}
    )
    tasks_done = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": window_start}, "status": "done"}
    )
    tasks_failed = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": window_start}, "status": "failed"}
    )
    success_rate = (
        round((tasks_done / tasks_total * 100), 1) if tasks_total else 0
    )

    # Mode distribution (A/B/C/D/E/F) from ora_council_logs
    mode_dist: dict = {}
    async for r in db.ora_council_logs.aggregate(
        [
            {"$match": {"ts": {"$gte": window_start}}},
            {"$group": {"_id": "$mode", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
    ):
        mid = r.get("_id")
        if mid:
            mode_dist[mid] = int(r.get("count", 0))

    # Daily active users trend (last 14 days)
    dau_trend = []
    for i in range(13, -1, -1):
        day_start = now - ((i + 1) * 86400)
        day_end = now - (i * 86400)
        day_count = await _first_or_zero(
            db.chat_sessions,
            [
                {"$match": {"updated_at": {"$gte": day_start, "$lt": day_end}}},
                {"$group": {"_id": "$user_id"}},
                {"$count": "c"},
            ],
            "c",
        )
        label = datetime.fromtimestamp(day_end, tz=timezone.utc).strftime("%b %d")
        dau_trend.append({"date": label, "users": day_count})

    # Top features used (by chat mode)
    feature_labels = {
        "C": "Code Ship", "D": "Debug", "B": "Advice",
        "A": "Chat", "E": "Audit", "F": "Engage",
    }
    top_features = [
        {"mode": k, "label": feature_labels.get(k, k), "count": v}
        for k, v in sorted(mode_dist.items(), key=lambda x: -x[1])
    ]

    # Token burn (last N days)
    tokens_burned = await _first_or_zero(
        db.cto_tasks,
        [
            {"$match": {"created_at": {"$gte": window_start}, "status": "done"}},
            {"$group": {"_id": None, "total": {"$sum": "$tokens_used"}}},
        ],
        "total",
    )

    # Tier breakdown
    tier_breakdown: dict = {}
    async for r in db.dev_users.aggregate(
        [
            {"$group": {"_id": "$tier", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
    ):
        tier_breakdown[r.get("_id") or "unknown"] = int(r.get("count", 0))

    # Maxx mode usage
    maxx_tasks = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": window_start}, "maxx_mode": True}
    )

    return {
        "ok": True,
        "period_days": days,
        "users": {
            "total": total_users,
            "dau": dau,
            "wau": wau,
            "mau": mau,
            "new_this_week": new_users_week,
            "by_tier": tier_breakdown,
        },
        "tasks": {
            "total": tasks_total,
            "done": tasks_done,
            "failed": tasks_failed,
            "success_rate_pct": success_rate,
            "maxx_mode": maxx_tasks,
            "tokens_burned": tokens_burned,
        },
        "modes": {
            "distribution": mode_dist,
            "top_features": top_features,
        },
        "trend": {
            "dau_14d": dau_trend,
        },
    }


# ── Cache stats — observability for in-memory route cache ─────────────
@router.get("/cache/stats")
async def cache_stats(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Iter 140 — in-memory route cache observability. Returns the
    configured routes and currently-live entries with their remaining
    TTLs. Helps validate cache hit rates from the admin dashboard
    without scraping logs."""
    await _require_admin(authorization)
    from services.route_cache import _CACHE, ROUTE_CONFIG
    import time as _t
    now = _t.time()
    entries = []
    for key, (expires_at, status, body, _ctype) in list(_CACHE.items()):
        ttl_remaining = max(0, expires_at - now)
        entries.append({
            "key": key[:80],
            "ttl_remaining_s": round(ttl_remaining, 1),
            "size_bytes": len(body),
            "status": status,
        })
    return {
        "ok": True,
        "cached_routes": len(ROUTE_CONFIG),
        "live_entries": len(entries),
        "entries": sorted(entries, key=lambda x: -x["ttl_remaining_s"]),
    }


# ── Feature flags — MongoDB-backed kill switches / canaries ───────────
@router.get("/feature-flags")
async def list_feature_flags(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """List all feature flags and their status."""
    await _require_admin(authorization)
    from services.feature_flags import get_all_flags as _get_all_flags
    flags = await _get_all_flags()
    return {"ok": True, "flags": flags}


@router.post("/feature-flags/{flag}/toggle")
async def toggle_feature_flag(
    flag: str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Flip a feature flag's `enabled` boolean."""
    await _require_admin(authorization)
    db = require_db()
    doc = await db.feature_flags.find_one({"flag": flag})
    if not doc:
        raise HTTPException(404, f"Flag '{flag}' not found")
    new_state = not doc.get("enabled", False)
    await db.feature_flags.update_one(
        {"flag": flag}, {"$set": {"enabled": new_state}}
    )
    from services.feature_flags import invalidate_cache as _ff_invalidate
    _ff_invalidate()
    return {"ok": True, "flag": flag, "enabled": new_state}


@router.post("/feature-flags")
async def create_feature_flag(
    body: dict,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Create or update a feature flag (idempotent upsert)."""
    await _require_admin(authorization)
    db = require_db()
    flag = (body.get("flag") or "").strip()
    if not flag:
        raise HTTPException(400, "flag name required")
    await db.feature_flags.update_one(
        {"flag": flag},
        {"$set": {
            "flag": flag,
            "enabled": bool(body.get("enabled", False)),
            "tier_allowlist": list(body.get("tier_allowlist") or []),
            "user_allowlist": list(body.get("user_allowlist") or []),
            "description": str(body.get("description") or ""),
        }},
        upsert=True,
    )
    from services.feature_flags import invalidate_cache as _ff_invalidate
    _ff_invalidate()
    return {"ok": True, "flag": flag}


# ── Brain replay — sandbox "what would ORA say" without committing ───
@router.post("/brain/{project_id}/replay")
async def admin_brain_replay(
    project_id: str,
    payload: dict,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Read-only ORA tester. Given a project's assembled brain context,
    answer a question without writing to MongoDB, without invoking
    Vanguard/Mode-D, and without firing any commit. Used to debug
    'ORA gave a wrong answer' cases — founder can iterate on the
    question text and see how ORA's response changes.
    """
    await _require_admin(authorization)
    question = (payload or {}).get("question", "").strip()
    if not question:
        raise HTTPException(400, "question required")
    if len(question) > 2000:
        raise HTTPException(400, "question too long (max 2000 chars)")

    db = require_db()
    proj = await db.cto_projects.find_one({"project_id": project_id})
    if not proj:
        raise HTTPException(404, "Project not found")

    # Match the PAT resolution used by the real chat path so the replay
    # answer is comparable to what a user would actually get.
    token = None
    try:
        from routers.cto_projects import _decrypt_pat, _user_gh_token
        token = await _decrypt_pat(proj["user_id"], proj.get("github_token")) \
            or await _user_gh_token(proj["user_id"])
    except Exception:
        token = None

    from services.project_brain import get_brain_context
    from services.llm import call_llm

    repo_full = f"{proj.get('github_owner', '')}/{proj.get('github_repo', '')}"
    brain_ctx = await get_brain_context(
        db, project_id, repo_full, github_token=token,
    )

    system = (
        "You are ORA, AUREM's AI engineer. You know this about the project:\n\n"
        + (brain_ctx or "(no project memory recorded yet)")
        + "\n\nAnswer the user's question directly using only the context "
          "above. Do not write code, do not propose commits — this is a "
          "read-only diagnostic session."
    )
    try:
        answer = await call_llm(
            messages=[{"role": "user", "content": question}],
            system=system, max_tokens=600, temperature=0.2,
        )
    except Exception as e:
        raise HTTPException(502, f"LLM call failed: {e}")

    return {
        "project_id":    project_id,
        "question":      question,
        "answer":        answer,
        "brain_chars":   len(brain_ctx),
        "context_used":  bool(brain_ctx),
    }



@router.delete("/project-brain/{project_id}/preference")
async def admin_brain_delete_preference(
    project_id: str,
    preference: str,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.project_brain import delete_preference


# ── Iter 48 — Sentry test endpoint ────────────────────────────────────
# Founder-only. Hit this once after adding SENTRY_DSN to prod env to
# confirm the integration works end-to-end. Look at sentry.io's Issues
# tab — you should see the test event within seconds.
@router.post("/sentry/test")
async def sentry_test(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    import os as _os
    if not _os.environ.get("SENTRY_DSN", "").strip():
        return {"ok": False, "active": False,
                "message": "SENTRY_DSN not set — add it to backend env and restart."}
    try:
        import sentry_sdk
        sentry_sdk.capture_message(
            "AUREM Sentry test — if you see this, monitoring is live ✓",
            level="info",
        )
        # Also fire a captured exception
        try:
            raise RuntimeError("AUREM Sentry test exception (intentional)")
        except RuntimeError as _re:
            sentry_sdk.capture_exception(_re)
        return {"ok": True, "active": True,
                "message": "Sent test event + exception to Sentry. Check the Issues tab."}
    except Exception as e:
        return {"ok": False, "active": False, "error": str(e)}


# ── Iter 63 — Cache purge & frontend refresh ────────────────────────────
@router.post("/cache/purge")
async def purge_caches(
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Real, fully-wired cache purge — admin-only.

    Clears:
      1. Cloudflare edge cache  (if CLOUDFLARE_API_TOKEN + ZONE_ID set)
      2. In-memory `lru_cache` of skill_context_injector
      3. MongoDB TTL caches: repo_context_cache, github_issues_cache,
         codebase_index_cache (collections used as caches; safe to drop
         rows — they self-rebuild on next read).

    Returns a structured report so the UI can show exactly what landed.
    The frontend then performs its own client-side step (unregister SWs,
    `caches.delete()`, hard reload).
    """
    import os
    import httpx
    await _require_admin(authorization)

    report = {
        "cloudflare": {"status": "skipped", "detail": "CLOUDFLARE_API_TOKEN / ZONE_ID not set"},
        "lru_cache": {"status": "skipped", "detail": ""},
        "mongo_caches": {},
    }

    # 1. Cloudflare edge purge — only if env configured
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    cf_zone = os.environ.get("CLOUDFLARE_ZONE_ID")
    if cf_token and cf_zone:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://api.cloudflare.com/client/v4/zones/{cf_zone}/purge_cache",
                    headers={
                        "Authorization": f"Bearer {cf_token}",
                        "Content-Type": "application/json",
                    },
                    json={"purge_everything": True},
                )
                cf_body = resp.json()
                if resp.status_code == 200 and cf_body.get("success"):
                    report["cloudflare"] = {
                        "status": "ok",
                        "detail": "Purge_everything fired — edge cache will refill on next request.",
                    }
                else:
                    report["cloudflare"] = {
                        "status": "error",
                        "detail": str(cf_body.get("errors") or cf_body)[:300],
                    }
        except Exception as e:
            report["cloudflare"] = {"status": "error", "detail": str(e)[:300]}

    # 2. In-memory lru_cache on skill injector
    try:
        from services.skill_context_injector import _load_skill
        _load_skill.cache_clear()
        report["lru_cache"] = {
            "status": "ok",
            "detail": "skill_context_injector._load_skill lru_cache cleared",
        }
    except Exception as e:
        report["lru_cache"] = {"status": "error", "detail": str(e)[:300]}

    # 3. Mongo TTL caches — drop docs so the next read repopulates
    db = get_db()
    if db is not None:
        for coll_name in (
            "repo_context_cache",
            "github_issues_cache",
            "codebase_index_cache",
        ):
            try:
                r = await db[coll_name].delete_many({})
                report["mongo_caches"][coll_name] = {
                    "status": "ok", "deleted": r.deleted_count,
                }
            except Exception as e:
                report["mongo_caches"][coll_name] = {
                    "status": "error", "detail": str(e)[:200],
                }
    else:
        report["mongo_caches"] = {"status": "skipped", "detail": "no DB"}

    return {"ok": True, "report": report}



# ── Iter 98 — Live Integration Health Center ───────────────────────────
# Real-time probes of every external dependency. Cached in Mongo so the
# UI is fast; refreshed automatically once daily and on-demand by the
# founder via POST /admin/integrations/refresh.
@router.get("/integrations/health")
async def integrations_health(
    authorization: Optional[str] = Header(None),
):
    """Return the latest cached snapshot of every integration probe.
    If no snapshot exists yet, run all probes inline (slow first hit)."""
    await _require_admin(authorization)
    db = require_db()
    snap = await db.integration_health.find_one(
        {"_id": "latest"}, {"_id": 0}
    )
    if not snap:
        # Cold start — probe immediately so the founder sees real data.
        from services.integration_health import run_all_probes, summary_counts
        results = await run_all_probes()
        snap = {
            "results":      results,
            "summary":      summary_counts(results),
            "generated_at": time.time(),
            "trigger":      "cold_start",
        }
        await db.integration_health.update_one(
            {"_id": "latest"},
            {"$set": snap},
            upsert=True,
        )
    return snap


@router.post("/integrations/refresh")
async def integrations_refresh(
    authorization: Optional[str] = Header(None),
):
    """Force-re-probe every integration NOW. Founder-only — each call
    actually hits all the external APIs."""
    await _require_admin(authorization)
    from services.integration_health import run_all_probes, summary_counts
    results = await run_all_probes()
    snap = {
        "results":      results,
        "summary":      summary_counts(results),
        "generated_at": time.time(),
        "trigger":      "manual",
    }
    db = require_db()
    await db.integration_health.update_one(
        {"_id": "latest"},
        {"$set": snap},
        upsert=True,
    )
    # Iter 212m-17 — process new top-up alerts inline so the founder
    # gets an immediate email when a refresh surfaces a broken probe
    # (instead of waiting for the next daily cron at 06:00 UTC).
    try:
        from services.topup_alerts import process_snapshot
        alert_result = await process_snapshot(db, snap)
        snap["alerts_processed"] = alert_result
    except Exception as e:
        logger.warning(f"topup_alerts on manual refresh: {e!r}")
    # Iter 212m-16 — return the fresh snapshot so the admin UI can
    # render the result without a second roundtrip to /integrations/health.
    return snap


# ── Iter 212m-17 — Top-up Alerts admin endpoints ────────────────────────


@router.get("/alerts")
async def list_alerts(
    status: str = "active",
    authorization: Optional[str] = Header(None),
):
    """List integration top-up alerts. `status` filter accepts
    `active` (default), `resolved`, `dismissed`, or `all`."""
    await _require_admin(authorization)
    db = require_db()
    query: dict = {}
    if status != "all":
        query["status"] = status
    rows = await db.topup_alerts.find(
        query, {"_id": 0}
    ).sort("first_seen", -1).limit(100).to_list(100)
    counts = {
        "active":    await db.topup_alerts.count_documents({"status": "active"}),
        "critical":  await db.topup_alerts.count_documents(
            {"status": "active", "severity": "critical"}
        ),
        "warning":   await db.topup_alerts.count_documents(
            {"status": "active", "severity": "warning"}
        ),
    }
    return {"alerts": rows, "counts": counts}


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(
    alert_id: str,
    authorization: Optional[str] = Header(None),
):
    """Manually dismiss an alert — admin acknowledged + actioned. Does
    NOT prevent the same alert from firing again tomorrow if the
    integration is still in the same state (the dedupe key is per-day)."""
    await _require_admin(authorization)
    db = require_db()
    r = await db.topup_alerts.update_one(
        {"alert_id": alert_id},
        {"$set": {"status": "dismissed", "dismissed_at": time.time()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Alert not found")
    return {"ok": True, "alert_id": alert_id, "status": "dismissed"}


# ── Iter 100 — Live Financial Command Center ───────────────────────────
# Real MongoDB → metrics, editable settings, FX-aware presentation.
@router.get("/financials")
async def admin_financials(
    authorization: Optional[str] = Header(None),
):
    """Live financial dashboard payload — pulls real user counts from
    `dev_users`, real payments from `cto_payments`, real Maxx usage,
    blends with editable settings and current USD→CAD FX."""
    await _require_admin(authorization)
    from services.financials import compute_financials
    db = require_db()
    return await compute_financials(db)


@router.post("/financials/settings")
async def admin_financials_save(
    payload: dict,
    authorization: Optional[str] = Header(None),
):
    """Persist the founder's editable financial inputs (cash on hand,
    dev salary, manual user overrides for hypotheticals). Founder-only."""
    await _require_admin(authorization)
    from services.financials import save_settings, compute_financials
    db = require_db()
    await save_settings(db, payload or {})
    # Return a full re-computed payload so the UI updates atomically.


# ── Iter 102 — Manual trigger for end-of-month overage billing ─────────
# Defensive: founder can run the cron on-demand if the scheduled 1st-of-
# month tick was missed (e.g., backend was down, redeploy in progress).
@router.post("/billing/run-overage-cron")
async def admin_run_overage_cron(
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.billing_cron import bill_maxx_overages
    db = require_db()
    result = await bill_maxx_overages(db)
    await db.billing_cron_runs.insert_one({**result, "trigger": "manual"})
    return result

    return await compute_financials(db)

    # Also append to history (tiny, last 100 snapshots)
    await db.integration_health_history.insert_one({
        **snap,
        "_id": f"snap_{int(snap['generated_at'])}",
    })
    return snap


# ── Vanguard audit log (iter 112) ──────────────────────────────────
@router.get("/vanguard/stats")
async def vanguard_stats(
    days: int = 7,
    authorization: Optional[str] = Header(None),
):
    """Stats for the admin Vanguard dashboard:
      total blocked this window, top rule, by-rule / by-project /
      by-severity breakdowns, day-bucketed sparkline."""
    await _require_admin(authorization)
    db = get_db()
    from services.vanguard_audit import weekly_stats
    return await weekly_stats(db, since_days=max(1, min(days, 90)))


@router.get("/vanguard/recent")
async def vanguard_recent(
    limit: int = 25,
    authorization: Optional[str] = Header(None),
):
    """Most recent N blocked-commit rows for the table."""
    await _require_admin(authorization)

# ── DB health (iter 117) ───────────────────────────────────────────
@router.get("/db-health")
async def db_health(authorization: Optional[str] = Header(None)):
    """Live DB health snapshot — verifies all required collections are
    materialised + the documented indexes exist. Reads the bootstrap
    state from the last init_prod_collections() run + re-checks the
    current collection set right now."""
    await _require_admin(authorization)
    from scripts.init_prod_collections import (
        get_last_bootstrap, _BOOTSTRAP_SPEC,
    )
    db = get_db()
    required = [name for name, _ in _BOOTSTRAP_SPEC]
    present: list[str] = []
    missing: list[str] = list(required)
    indexes_ok = True
    if db is not None:
        try:
            existing = set(await db.list_collection_names())
            present = [n for n in required if n in existing]
            missing = [n for n in required if n not in existing]
            # Spot-check: each required collection should have at least
            # one secondary index (beyond the default _id one). If any
            # collection has only _id, the boot script didn't run cleanly.
            for name, idx_specs in _BOOTSTRAP_SPEC:
                if name not in existing or not idx_specs:
                    continue
                idx = await db[name].list_indexes().to_list(length=50)
                if len(idx) < 1 + 1:  # _id_ + at least one user index
                    indexes_ok = False
                    break
        except Exception as e:
            return {
                "ok": False,
                "collections_present": 0,
                "last_bootstrap": None,
                "missing": required,
                "indexes_ok": False,
                "error": str(e)[:200],
            }
    last = get_last_bootstrap()
    return {
        "ok": True,
        "collections_present": len(present),
        "collections_expected": len(required),
        "last_bootstrap":        (last or {}).get("ts"),
        "last_bootstrap_summary": {
            "created":      (last or {}).get("created", []),
            "indexed_count": len((last or {}).get("indexed", [])),
            "errors":       (last or {}).get("errors", []),
        },
        "missing":    missing,
        "indexes_ok": indexes_ok and not missing,
    }

    db = get_db()
    from services.vanguard_audit import recent_blocks
    return {"rows": await recent_blocks(db, limit=max(1, min(limit, 200)))}



# ── Iter 123b — ORA skill usage analytics ────────────────────────────
# Industry research says <18 skills is optimal. We're at 22. After
# 2 weeks of live traffic this endpoint surfaces which skills are
# pulling weight so the founder can prune confidently.

@router.get("/skills-usage")
async def skills_usage(
    days: int = 14,
    authorization: Optional[str] = Header(None),
):
    """Aggregate ora_skill_usage over the last N days.

    Returns per-skill: call count, success rate, p50/p95 elapsed_ms.
    Use to identify dead-weight skills (<2% of calls) for pruning.
    """
    await _require_admin(authorization)
    db = require_db()

    days = max(1, min(int(days or 14), 90))
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    pipeline = [
        {"$match": {"ts": {"$gte": cutoff}}},
        {"$group": {
            "_id":       "$tool",
            "count":     {"$sum": 1},
            "ok_count":  {"$sum": {"$cond": ["$ok", 1, 0]}},
            "elapsed":   {"$push": "$elapsed_ms"},
        }},
        {"$sort": {"count": -1}},
    ]
    rows = []
    total = 0
    async for r in db.ora_skill_usage.aggregate(pipeline):
        # p50/p95 in Python — collection is small (<100k entries even at scale)
        elapsed = sorted(e for e in (r.get("elapsed") or []) if isinstance(e, (int, float)))
        def _pct(arr, p):
            if not arr:
                return None
            i = max(0, min(len(arr) - 1, int(len(arr) * p) - 1))
            return arr[i]
        rows.append({
            "tool":     r["_id"],
            "count":    r["count"],
            "ok_rate":  round(r["ok_count"] / r["count"], 3) if r["count"] else 0,
            "p50_ms":   _pct(elapsed, 0.50),
            "p95_ms":   _pct(elapsed, 0.95),
        })
        total += r["count"]

    # Annotate share so the founder can see at-a-glance which skills < 2%
    for row in rows:
        row["share"] = round(row["count"] / total, 3) if total else 0
        row["dead_weight"] = row["share"] < 0.02   # the prune threshold

    return {
        "window_days": days,
        "total_calls": total,
        "skills":      rows,
        "hint":        "skills with share<0.02 are prune candidates (industry ceiling target: 18 skills)",
    }


# ─── Iter 188 — extended overview metrics + new admin surfaces ────────
#
# Single aggregator endpoint that fuels the new metric cards on the
# Overview tab AND the new sidebar sections (MCP Usage, Warm Start,
# Graph Status, Agent Performance, Post-scan Issues, Revenue). One
# round-trip → multiple cards. All counts are scoped to the last
# 24 h / 7 d windows defined inline so the UI can render
# date-stamped chips without doing date math.

@router.get("/overview-metrics")
async def admin_overview_metrics(
    authorization: Optional[str] = Header(None),
):
    """Extended metrics for the Admin Overview tab.

    Returns one flat object with every metric the new cards need:
      - active_users_today, tasks_today, tasks_done_today
      - avg_task_seconds (over last 100 done tasks)
      - mcp_keys_total / mcp_keys_active_30d
      - warm_starts_24h with success_rate_pct
      - postscan_findings_7d (critical + warning split)
      - most_active_project (name + task_count last 7 d)
      - mode_distribution_30d (swift/pro/maxx counts)
      - revenue_30d (sum of completed cto_payments)
    """
    await _require_admin(authorization)
    db = require_db()

    now = time.time()
    day_ago = now - 86_400
    week_ago = now - 7 * 86_400
    month_ago = now - 30 * 86_400

    # Active users today — distinct user_id touching cto_tasks in 24 h.
    active_users_today = 0
    try:
        active_users_today = len(
            await db.cto_tasks.distinct(
                "user_id", {"created_at": {"$gte": day_ago}}
            )
        )
    except Exception as e:
        logger.warning("overview-metrics: active_users_today: %r", e)

    tasks_today = 0
    tasks_done_today = 0
    try:
        tasks_today = await db.cto_tasks.count_documents(
            {"created_at": {"$gte": day_ago}}
        )
        tasks_done_today = await db.cto_tasks.count_documents(
            {"created_at": {"$gte": day_ago}, "status": "done"}
        )
    except Exception as e:
        logger.warning("overview-metrics: tasks_today: %r", e)

    # Average task time over the last 100 completed tasks. Computed as
    # finished_at - created_at when both are present; sub-second values
    # ignored so a misset timestamp doesn't drag the mean down.
    avg_task_seconds = 0
    try:
        pipeline = [
            {"$match": {"status": "done",
                        "created_at": {"$gte": month_ago},
                        "finished_at": {"$exists": True}}},
            {"$sort": {"finished_at": -1}},
            {"$limit": 100},
            {"$project": {
                "_id": 0,
                "secs": {"$subtract": ["$finished_at", "$created_at"]},
            }},
            {"$match": {"secs": {"$gt": 1}}},
            {"$group": {"_id": None, "avg": {"$avg": "$secs"}}},
        ]
        async for row in db.cto_tasks.aggregate(pipeline):
            avg_task_seconds = round(float(row.get("avg") or 0), 2)
            break
    except Exception as e:
        logger.warning("overview-metrics: avg_task_seconds: %r", e)

    # MCP usage — keys minted via the /mcp/keys flow. The api_keys
    # collection holds rows shaped {key, user_id, client_id, scope,
    # last_used_at, …}.
    mcp_keys_total = 0
    mcp_keys_active_30d = 0
    try:
        mcp_keys_total = await db.api_keys.count_documents({})
        mcp_keys_active_30d = await db.api_keys.count_documents(
            {"last_used_at": {"$gte": month_ago}}
        )
    except Exception as e:
        logger.warning("overview-metrics: mcp_keys: %r", e)

    # Warm-start success rate over 24 h.
    warm_total_24h = 0
    warm_done_24h = 0
    warm_success_rate_pct = 0
    try:
        warm_total_24h = await db.warm_start_jobs.count_documents(
            {"created_at": {"$gte": day_ago}}
        )
        warm_done_24h = await db.warm_start_jobs.count_documents(
            {"created_at": {"$gte": day_ago}, "status": "done"}
        )
        if warm_total_24h:
            warm_success_rate_pct = round(
                100 * warm_done_24h / warm_total_24h, 1
            )
    except Exception as e:
        logger.warning("overview-metrics: warm_start: %r", e)

    # Post-scan issues last 7 d — vanguard findings recorded after
    # each task. Counts critical (block) vs warning (notify) buckets.
    postscan_critical_7d = 0
    postscan_warning_7d = 0
    try:
        postscan_critical_7d = await db.post_task_scans.count_documents(
            {"created_at": {"$gte": week_ago}, "severity": "critical"}
        )
        postscan_warning_7d = await db.post_task_scans.count_documents(
            {"created_at": {"$gte": week_ago},
             "severity": {"$in": ["warning", "warn"]}}
        )
    except Exception as e:
        logger.warning("overview-metrics: postscan: %r", e)

    # Most active project over the last 7 d.
    most_active_project = {"name": None, "task_count": 0}
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": week_ago},
                        "project_id": {"$ne": None}}},
            {"$group": {"_id": "$project_id", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": 1},
        ]
        async for row in db.cto_tasks.aggregate(pipeline):
            pid = row.get("_id")
            n = int(row.get("n") or 0)
            proj = await db.cto_projects.find_one(
                {"project_id": pid}, {"_id": 0, "name": 1}
            )
            most_active_project = {
                "name": (proj or {}).get("name") or pid,
                "task_count": n,
            }
            break
    except Exception as e:
        logger.warning("overview-metrics: most_active: %r", e)

    # Mode distribution over 30 d (Swift / Pro / Maxx).
    mode_distribution_30d = {"swift": 0, "pro": 0, "maxx": 0}
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": month_ago},
                        "mode": {"$in": ["swift", "pro", "maxx"]}}},
            {"$group": {"_id": "$mode", "n": {"$sum": 1}}},
        ]
        async for row in db.cto_tasks.aggregate(pipeline):
            m = row.get("_id")
            if m in mode_distribution_30d:
                mode_distribution_30d[m] = int(row.get("n") or 0)
    except Exception as e:
        logger.warning("overview-metrics: mode_dist: %r", e)

    # Revenue over 30 d — sum of `amount` from completed payments.
    revenue_30d = 0.0
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": month_ago},
                        "status": {"$in": ["paid", "complete", "completed",
                                            "succeeded"]}}},
            {"$group": {"_id": None, "sum": {"$sum": "$amount"}}},
        ]
        async for row in db.cto_payments.aggregate(pipeline):
            revenue_30d = round(float(row.get("sum") or 0), 2)
            break
    except Exception as e:
        logger.warning("overview-metrics: revenue_30d: %r", e)

    return {
        "active_users_today":     active_users_today,
        "tasks_today":            tasks_today,
        "tasks_done_today":       tasks_done_today,
        "avg_task_seconds":       avg_task_seconds,
        "mcp_keys_total":         mcp_keys_total,
        "mcp_keys_active_30d":    mcp_keys_active_30d,
        "warm_total_24h":         warm_total_24h,
        "warm_done_24h":          warm_done_24h,
        "warm_success_rate_pct":  warm_success_rate_pct,
        "postscan_critical_7d":   postscan_critical_7d,
        "postscan_warning_7d":    postscan_warning_7d,
        "most_active_project":    most_active_project,
        "mode_distribution_30d":  mode_distribution_30d,
        "revenue_30d":            revenue_30d,
        "generated_at":           now,
    }


# Lightweight list endpoints powering the new sidebar sections.

@router.get("/mcp-usage")
async def admin_mcp_usage(
    authorization: Optional[str] = Header(None),
    limit: int = 50,
):
    """Recent MCP API keys with usage timestamps for the MCP Usage tab."""
    await _require_admin(authorization)
    db = require_db()
    rows: list[dict] = []
    try:
        cursor = db.api_keys.find(
            {},
            {"_id": 0, "user_id": 1, "client_id": 1, "scope": 1,
             "created_at": 1, "last_used_at": 1, "expires_at": 1, "key": 1},
            sort=[("last_used_at", -1), ("created_at", -1)],
            limit=max(1, min(int(limit or 50), 200)),
        )
        async for r in cursor:
            k = r.get("key") or ""
            r["key_tail"] = k[-6:] if k else ""
            r.pop("key", None)
            rows.append(r)
    except Exception as e:
        logger.warning("admin/mcp-usage: %r", e)
    return {"rows": rows, "count": len(rows)}


@router.get("/warm-start-stats")
async def admin_warm_start_stats(
    authorization: Optional[str] = Header(None),
):
    """Warm-start latency & success metrics for the dedicated tab.

    `avg_seconds` covers the last 100 done jobs over 30 d; the breakdown
    shows the per-status counts over 7 d so we can spot a stuck queue."""
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    week_ago = now - 7 * 86_400
    month_ago = now - 30 * 86_400

    breakdown: dict[str, int] = {}
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": week_ago}}},
            {"$group": {"_id": "$status", "n": {"$sum": 1}}},
        ]
        async for row in db.warm_start_jobs.aggregate(pipeline):
            breakdown[str(row.get("_id") or "unknown")] = int(row.get("n") or 0)
    except Exception as e:
        logger.warning("admin/warm-start-stats breakdown: %r", e)

    avg_seconds = 0.0
    try:
        pipeline = [
            {"$match": {"status": "done",
                        "created_at": {"$gte": month_ago},
                        "finished_at": {"$exists": True}}},
            {"$sort": {"finished_at": -1}},
            {"$limit": 100},
            {"$project": {"_id": 0,
                          "secs": {"$subtract": ["$finished_at", "$created_at"]}}},
            {"$match": {"secs": {"$gt": 0.1}}},
            {"$group": {"_id": None, "avg": {"$avg": "$secs"}}},
        ]
        async for row in db.warm_start_jobs.aggregate(pipeline):
            avg_seconds = round(float(row.get("avg") or 0), 2)
            break
    except Exception as e:
        logger.warning("admin/warm-start-stats avg: %r", e)

    return {
        "avg_seconds":  avg_seconds,
        "breakdown_7d": breakdown,
        "window_days":  7,
    }


@router.get("/graph-status")
async def admin_graph_status(
    authorization: Optional[str] = Header(None),
    limit: int = 60,
):
    """Which projects have a Knowledge Graph built (and how recently)."""
    await _require_admin(authorization)
    db = require_db()
    rows: list[dict] = []
    try:
        cursor = db.cto_projects.find(
            {},
            {"_id": 0, "project_id": 1, "name": 1, "user_id": 1,
             "graph_built_at": 1, "graph_node_count": 1,
             "github_owner": 1, "github_repo": 1},
            sort=[("graph_built_at", -1), ("created_at", -1)],
            limit=max(1, min(int(limit or 60), 200)),
        )
        async for r in cursor:
            r["has_graph"] = bool(r.get("graph_built_at"))
            rows.append(r)
    except Exception as e:
        logger.warning("admin/graph-status: %r", e)
    return {"rows": rows, "count": len(rows)}


@router.get("/agent-performance")
async def admin_agent_performance(
    authorization: Optional[str] = Header(None),
):
    """Smart-router agent stats — model usage + per-mode latency."""
    await _require_admin(authorization)
    db = require_db()
    now = time.time()
    month_ago = now - 30 * 86_400

    per_model: list[dict] = []
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": month_ago},
                        "model": {"$ne": None}}},
            {"$group": {
                "_id": "$model",
                "n": {"$sum": 1},
                "avg_secs": {"$avg": {
                    "$subtract": ["$finished_at", "$created_at"]
                }},
                "done": {
                    "$sum": {"$cond": [{"$eq": ["$status", "done"]}, 1, 0]}
                },
            }},
            {"$sort": {"n": -1}},
            {"$limit": 20},
        ]
        async for row in db.cto_tasks.aggregate(pipeline):
            per_model.append({
                "model":     row.get("_id"),
                "calls":     int(row.get("n") or 0),
                "done":      int(row.get("done") or 0),
                "avg_secs":  round(float(row.get("avg_secs") or 0), 2),
            })
    except Exception as e:
        logger.warning("admin/agent-performance: %r", e)

    return {"per_model_30d": per_model}


@router.get("/postscan-issues")
async def admin_postscan_issues(
    authorization: Optional[str] = Header(None),
    limit: int = 50,
):
    """Recent post-task scanner findings (vanguard regex + lint)."""
    await _require_admin(authorization)
    db = require_db()
    rows: list[dict] = []
    try:
        cursor = db.post_task_scans.find(
            {},
            {"_id": 0, "task_id": 1, "project_id": 1, "user_id": 1,
             "severity": 1, "rule": 1, "file": 1, "match": 1,
             "created_at": 1},
            sort=[("created_at", -1)],
            limit=max(1, min(int(limit or 50), 200)),
        )
        async for r in cursor:
            m = (r.get("match") or "")[:80]
            r["match"] = m
            rows.append(r)
    except Exception as e:
        logger.warning("admin/postscan-issues: %r", e)
    return {"rows": rows, "count": len(rows)}



# ─── Iter 191 — Stripe API key admin panel + live ping ─────────────────
#
# GET  /admin/stripe-config   — current key (masked) + live ping status
# POST /admin/stripe-config   — validate + save a new key, hot-swap at
#                                runtime via payments.set_runtime_stripe_key
#
# The current key resolution prefers (in order): runtime override (set
# at boot from this DB row), env var, .env file. The admin panel always
# writes to MongoDB so changes survive across replica pods AND across
# deploys without touching the secrets manager.

@router.get("/stripe-config")
async def admin_get_stripe_config(
    authorization: Optional[str] = Header(None),
):
    """Return the current Stripe key (masked) + live ping result."""
    await _require_admin(authorization)
    from routers.payments import _stripe_key, set_runtime_stripe_key
    import stripe as _stripe

    db = require_db()

    # If there's an admin override in DB and it hasn't been loaded yet,
    # load it now so the green/red light reflects the actual key in use.
    db_key = ""
    try:
        row = await db.admin_settings.find_one({"_id": "stripe_api_key"})
        if row:
            db_key = (row.get("value") or "").strip()
            if db_key:
                set_runtime_stripe_key(db_key)
    except Exception as e:
        logger.warning("admin/stripe-config: DB lookup failed: %r", e)

    key = _stripe_key()
    if not key:
        return {
            "configured": False,
            "status": "error",
            "error": "No Stripe key configured. Click Edit and paste your sk_live_… or sk_test_… key.",
            "source": "none",
            "last4": "",
            "mode": "unknown",
        }

    # Detect source for the UI badge.
    if db_key and db_key == key:
        source = "db_override"
    elif (os.environ.get("STRIPE_SECRET_KEY") or
          os.environ.get("STRIPE_API_KEY")) == key:
        source = "env"
    else:
        source = "dotenv"

    mode = "live" if key.startswith("sk_live_") else (
        "test" if key.startswith("sk_test_") else "unknown"
    )
    last4 = key[-4:] if len(key) >= 8 else ""

    # Live ping — Account.retrieve is the canonical "is this key valid"
    # check. Cheap, free of charge, and surfaces capability/restrictions.
    _stripe.api_key = key
    try:
        acct = await asyncio.to_thread(_stripe.Account.retrieve)
        return {
            "configured": True,
            "status":  "ok",
            "error":   "",
            "source":  source,
            "last4":   last4,
            "mode":    mode,
            "account": {
                "id":             acct.get("id"),
                "email":          acct.get("email"),
                "business_name":  acct.get("business_profile", {}).get("name")
                                   or acct.get("settings", {}).get("dashboard", {}).get("display_name")
                                   or "",
                "country":        acct.get("country"),
                "charges_enabled":  bool(acct.get("charges_enabled")),
                "payouts_enabled":  bool(acct.get("payouts_enabled")),
                "details_submitted": bool(acct.get("details_submitted")),
            },
        }
    except _stripe.error.AuthenticationError as e:
        return {
            "configured": True, "status": "error",
            "error": f"Invalid key — Stripe rejected authentication ({getattr(e,'user_message',None) or str(e)})",
            "source": source, "last4": last4, "mode": mode,
        }
    except _stripe.error.PermissionError as e:
        return {
            "configured": True, "status": "error",
            "error": f"Key is missing the `rak_read_only` or account-read permission ({e})",
            "source": source, "last4": last4, "mode": mode,
        }
    except _stripe.error.APIConnectionError as e:
        return {
            "configured": True, "status": "error",
            "error": f"Can't reach Stripe ({e}). Network or DNS issue from the deploy pod.",
            "source": source, "last4": last4, "mode": mode,
        }
    except _stripe.error.StripeError as e:
        return {
            "configured": True, "status": "error",
            "error": f"Stripe error: {getattr(e,'user_message',None) or str(e)}",
            "source": source, "last4": last4, "mode": mode,
        }
    except Exception as e:
        return {
            "configured": True, "status": "error",
            "error": f"Unexpected error: {e}",
            "source": source, "last4": last4, "mode": mode,
        }


@router.post("/stripe-config")
async def admin_set_stripe_config(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Validate a new Stripe key, save it to admin_settings, and
    hot-swap it into the running process. Refuses any key that fails
    a live Account.retrieve()."""
    user = await _require_admin(authorization)
    from routers.payments import set_runtime_stripe_key
    import stripe as _stripe

    new_key = ((body or {}).get("api_key") or "").strip()
    if not new_key:
        raise HTTPException(400, "api_key required")
    if not (new_key.startswith("sk_live_") or new_key.startswith("sk_test_")):
        raise HTTPException(400, "Key must start with sk_live_ or sk_test_")
    if new_key.startswith("sk_test_emergent"):
        raise HTTPException(400, "Refusing to save the Emergent sandbox placeholder")

    # Validate via live ping BEFORE saving — never persist a broken key.
    _stripe.api_key = new_key
    try:
        acct = await asyncio.to_thread(_stripe.Account.retrieve)
    except _stripe.error.AuthenticationError:
        raise HTTPException(400, "Stripe rejected this key — authentication failed")
    except _stripe.error.PermissionError as e:
        raise HTTPException(400, f"Key missing required permissions: {e}")
    except _stripe.error.StripeError as e:
        raise HTTPException(400, f"Stripe error: {getattr(e,'user_message',None) or str(e)}")
    except Exception as e:
        raise HTTPException(400, f"Could not validate key: {e}")

    # Persist + hot-swap.
    db = require_db()
    try:
        await db.admin_settings.update_one(
            {"_id": "stripe_api_key"},
            {"$set": {
                "_id":       "stripe_api_key",
                "value":     new_key,
                "updated_at": time.time(),
                "updated_by": user.get("email") or user.get("user_id"),
                "mode":      "live" if new_key.startswith("sk_live_") else "test",
                "account_id": acct.get("id"),
            }},
            upsert=True,
        )
    except Exception as e:
        logger.error("admin/stripe-config: DB save failed: %r", e)
        raise HTTPException(500, f"Saved to memory but DB persistence failed: {e}")

    set_runtime_stripe_key(new_key)
    logger.info("Stripe key hot-swapped by admin=%s account=%s mode=%s",
                user.get("email"), acct.get("id"),
                "live" if new_key.startswith("sk_live_") else "test")

    return {
        "ok": True,
        "last4": new_key[-4:],
        "mode": "live" if new_key.startswith("sk_live_") else "test",
        "account_id": acct.get("id"),
        "message": "Stripe key saved, validated, and now live.",
    }



# ─── Iter 193 — User delete + bulk email offers ────────────────────────
#
# DELETE /admin/users/{user_id}       — hard-delete a user + cascade
# POST   /admin/users/email-offer     — send offer email to N users via
#                                        Resend (one call, N parallel)
#
# Both endpoints require admin. Delete cascades to: cto_sessions,
# cto_projects, cto_tasks, cto_payments, api_keys, post_task_scans
# (all collections that hold a user_id). Founder accounts (allowlisted
# in FOUNDER_EMAILS) are refused — accidental founder wipe would lock
# us out of our own product.

@router.delete("/users/{user_id}")
async def admin_delete_user(
    user_id: str,
    authorization: Optional[str] = Header(None),
):
    """Hard-delete a user and cascade across owned collections."""
    actor = await _require_admin(authorization)
    db = require_db()

    # Look up the target — also gives us the email for the founder check
    # and audit log.
    target = await db.dev_users.find_one({"user_id": user_id}, {"_id": 0, "email": 1, "name": 1})
    if not target:
        raise HTTPException(404, "User not found")

    target_email = (target.get("email") or "").strip().lower()
    # Refuse to delete a founder. Founders are baked into FOUNDER_EMAILS
    # at deploy time; wiping one would brick login + billing.
    founder_list = [
        e.strip().lower() for e in
        (os.environ.get("FOUNDER_EMAILS") or "").split(",") if e.strip()
    ]
    if target_email and target_email in founder_list:
        raise HTTPException(403, f"Refusing to delete founder account ({target_email})")
    # Belt + suspenders — never let an admin delete themselves either.
    if user_id == actor.get("user_id"):
        raise HTTPException(403, "Cannot delete your own account from this UI")

    deletions: dict[str, int] = {}
    # Collections that key off user_id. Each is deleted in its own
    # try/except so one failed collection doesn't block the rest.
    for coll, key in [
        ("dev_users",        "user_id"),
        ("cto_sessions",     "user_id"),
        ("chat_sessions",    "user_id"),
        ("cto_projects",     "user_id"),
        ("cto_tasks",        "user_id"),
        ("cto_payments",     "user_id"),
        ("api_keys",         "user_id"),
        ("post_task_scans",  "user_id"),
        ("warm_start_jobs",  "user_id"),
        ("oauth_codes",      "user_id"),
    ]:
        try:
            res = await db[coll].delete_many({key: user_id})
            deletions[coll] = res.deleted_count
        except Exception as e:
            logger.warning("admin_delete_user[%s]: %s failed: %r", user_id, coll, e)
            deletions[coll] = -1

    logger.info(
        "user deleted by admin=%s target_user=%s target_email=%s deletions=%s",
        actor.get("email"), user_id, target_email, deletions,
    )
    return {
        "ok":         True,
        "user_id":    user_id,
        "email":      target_email,
        "deletions":  deletions,
    }


@router.post("/users/email-offer")
async def admin_send_user_offer(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Send a one-off offer email to a list of users.

      Request body:
        {
          "user_ids": ["uid_..", "uid_.."],   # required, max 500
          "subject":  "Special offer for you",
          "body_html": "<p>Hi {{name}}, ...</p>",   # supports {{name}} and {{email}}
          "from": "ORA <ora@aurem.live>",   # optional, falls back to DIGEST_FROM
          "reply_to": "polarisbuiltinc@gmail.com",  # optional, falls back to support inbox
        }

    Returns: {sent, failed, dry_run, recipients[]}
    """
    actor = await _require_admin(authorization)
    db = require_db()

    user_ids = (body or {}).get("user_ids") or []
    subject  = ((body or {}).get("subject") or "").strip()
    body_html = ((body or {}).get("body_html") or "").strip()
    from_addr = ((body or {}).get("from") or "").strip()
    reply_to = ((body or {}).get("reply_to") or "").strip() or "polarisbuiltinc@gmail.com"

    if not isinstance(user_ids, list) or not user_ids:
        raise HTTPException(400, "user_ids[] required (non-empty)")
    if len(user_ids) > 500:
        raise HTTPException(400, "Too many recipients (max 500 per batch)")
    if not subject:
        raise HTTPException(400, "subject required")
    if not body_html:
        raise HTTPException(400, "body_html required")

    # Resolve emails. Project only what we need.
    cursor = db.dev_users.find(
        {"user_id": {"$in": user_ids}},
        {"_id": 0, "user_id": 1, "email": 1, "name": 1},
    )
    targets: list[dict] = []
    async for u in cursor:
        if u.get("email"):
            targets.append(u)

    if not targets:
        raise HTTPException(404, "No valid emails found for the given user_ids")

    resend_key = os.environ.get("RESEND_API_KEY") or ""
    sender = from_addr or os.environ.get("DIGEST_FROM") or os.environ.get(
        "RESEND_FROM_EMAIL"
    ) or "AUREM CTO <onboarding@resend.dev>"

    # When the API key is missing we record what *would* have been sent
    # so the UI can still display recipient counts (and ops can see what
    # was queued during a Resend outage).
    if not resend_key:
        logger.warning(
            "email-offer dry-run (no RESEND_API_KEY): admin=%s recipients=%d subject=%r",
            actor.get("email"), len(targets), subject,
        )
        return {
            "ok":         True,
            "dry_run":    True,
            "sent":       0,
            "failed":     0,
            "recipients": [t["email"] for t in targets],
            "reply_to":   reply_to,
            "note":       "RESEND_API_KEY not configured — no emails actually sent.",
        }

    # Fire all sends concurrently. Per-recipient template substitution.
    import httpx

    async def _send_one(target: dict) -> tuple[str, bool, str]:
        name = (target.get("name") or "").strip() or "there"
        email = target["email"]
        personalized = (body_html
                        .replace("{{name}}", name)
                        .replace("{{email}}", email))
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {resend_key}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "from":    sender,
                        "to":      [email],
                        "subject": subject,
                        "html":    personalized,
                    },
                )
            if resp.status_code < 300:
                return (email, True, "")
            return (email, False, f"http_{resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            return (email, False, str(e)[:200])

    results = await asyncio.gather(
        *[_send_one(t) for t in targets],
        return_exceptions=False,
    )
    sent = sum(1 for _, ok, _ in results if ok)
    failed = [{"email": e, "error": err}
              for e, ok, err in results if not ok]

    # Persist a record so support can audit what we promised users.
    try:
        await db.email_offers.insert_one({
            "_id":        f"offer_{int(time.time())}_{actor.get('user_id', 'anon')}",
            "admin_id":   actor.get("user_id"),
            "admin_email": actor.get("email"),
            "subject":    subject,
            "body_html":  body_html,
            "from":       sender,
            "reply_to":   reply_to,
            "recipient_count": len(targets),
            "sent_count":      sent,
            "failed_count":    len(failed),
            "failed":     failed[:50],   # cap for storage hygiene
            "created_at": time.time(),
        })
    except Exception as e:
        logger.warning("email-offer ledger insert failed: %r", e)

    logger.info(
        "email-offer sent by admin=%s recipients=%d sent=%d failed=%d subject=%r",
        actor.get("email"), len(targets), sent, len(failed), subject,
    )
    return {
        "ok":         True,
        "dry_run":    False,
        "sent":       sent,
        "failed":     len(failed),
        "failed_detail": failed[:20],
        "recipients": [t["email"] for t in targets],
        "reply_to":   reply_to,
    }



# ─── Iter 196 — Activation funnel insights ─────────────────────────────
#
# GET /admin/insights/activation-funnel
#
# Filters out test/automation accounts (test@, qa-, audit_, e2e-, auto_,
# u_<hex>, @aurem.test) before computing signup → repo → task → paid
# conversion rates. Returns the real-user funnel plus a top-10 recent
# signups breakdown so the founder can scan activation at a glance
# without an SQL shell.
@router.get("/insights/activation-funnel")
async def activation_funnel(
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()

    import re

    # Email patterns that flag an account as test/automation. We use a
    # mix of substring + prefix checks so `audit_…@aurem.dev_PREVIEW`
    # and `auto_5fc97100fc@aurem.test` both get filtered out without
    # also catching a real customer who happens to have "auto" in
    # their handle.
    test_patterns = (
        "@aurem.test",  # anything on the synthetic domain
        "@aurem.dev_",  # PREVIEW/AUDIT suffixed rows
    )
    test_prefixes = (
        "test@", "test_", "qa-", "qa_",
        "audit_", "e2e-", "e2e_", "auto_",
        "oauth-", "oauth_", "mcp-", "mcp_",
    )
    test_prefix_regex = re.compile(r"^u_[a-f0-9]{6,16}@", re.I)

    def is_test(email: str | None) -> bool:
        e = (email or "").lower()
        if not e:
            return True  # blank email = synthetic
        if any(p in e for p in test_patterns):
            return True
        if any(e.startswith(p) for p in test_prefixes):
            return True
        if test_prefix_regex.match(e):
            return True
        return False

    all_users = await db.dev_users.find(
        {}, {"_id": 0, "user_id": 1, "email": 1,
             "tier": 1, "created_at": 1, "github": 1}
    ).to_list(2000)

    real_users = [u for u in all_users if not is_test(u.get("email"))]
    real_ids = {u["user_id"] for u in real_users if u.get("user_id")}

    # Step 2 — connected_github: any GitHub identity recorded on
    # dev_users (OAuth signup OR post-signup Connect via /github-oauth).
    # `github` is the nested doc — presence of `id`, `access_token`, or
    # `login` all count as "connected".
    github_uids = set()
    for u in real_users:
        gh = u.get("github") or {}
        if (gh.get("id") or gh.get("access_token") or gh.get("login")):
            uid = u.get("user_id")
            if uid:
                github_uids.add(uid)

    # Step 3 — added_project: at least one row in cto_projects.
    project_uids = set()
    cursor = db.cto_projects.find({}, {"_id": 0, "user_id": 1})
    async for p in cursor:
        uid = p.get("user_id")
        if uid in real_ids:
            project_uids.add(uid)

    # Step 4 — sent_message: at least one chat session with ≥1 turn.
    session_uids = set()
    cursor = db.chat_sessions.find(
        {"turns.0": {"$exists": True}},  # has at least 1 turn
        {"_id": 0, "user_id": 1},
    )
    async for s in cursor:
        uid = s.get("user_id")
        if uid in real_ids:
            session_uids.add(uid)

    # Step 5 — shipped_code: at least one task in `done` status.
    task_uids = set()
    cursor = db.cto_tasks.find(
        {"status": "done"}, {"_id": 0, "user_id": 1}
    )
    async for t in cursor:
        uid = t.get("user_id")
        if uid in real_ids:
            task_uids.add(uid)

    paying_tiers = {"starter", "pro", "team", "founder"}
    paying = [u for u in real_users
              if (u.get("tier") or "").lower() in paying_tiers]

    def pct(a: int, b: int) -> str:
        return f"{(a / max(b, 1)) * 100:.1f}%"

    def pct_num(a: int, b: int) -> float:
        # Funnel rate is bounded [0, 100]. When the previous step has
        # zero users we return 0 (instead of dividing by 1 and pretending
        # the rate is undefined). Capping at 100 means a "leaky" funnel
        # where users entered a later stage without crossing the prior
        # one displays as a full bar instead of >100 %.
        if b <= 0:
            return 0.0
        return round(min((a / b) * 100, 100), 1)

    # Iter 212m-3 — 5-step activation funnel with per-step conversion
    # rates. Biggest drop-off (largest delta between consecutive steps,
    # as %) is surfaced so the UI can highlight it red.
    n_signup = len(real_users)
    n_github = len(github_uids)
    n_proj   = len(project_uids)
    n_sess   = len(session_uids)
    n_task   = len(task_uids)

    funnel_steps = [
        {"key": "signed_up",       "label": "Signed up",        "count": n_signup},
        {"key": "connected_github","label": "Connected GitHub", "count": n_github},
        {"key": "added_project",   "label": "Added Project",    "count": n_proj},
        {"key": "sent_message",    "label": "Sent Message",     "count": n_sess},
        {"key": "shipped_code",    "label": "Shipped Code",     "count": n_task},
    ]

    # Compute conversion rate from previous step + absolute drop-off
    # (count_prev - count_current). Track which transition has the
    # largest drop so the UI can highlight it.
    biggest_drop_idx = -1
    biggest_drop_n = -1
    for i, step in enumerate(funnel_steps):
        if i == 0:
            step["pct_of_prev"] = 100.0
            step["drop_from_prev"] = 0
            continue
        prev_n = funnel_steps[i-1]["count"]
        cur_n  = step["count"]
        step["pct_of_prev"]    = pct_num(cur_n, prev_n)
        step["drop_from_prev"] = max(prev_n - cur_n, 0)
        if step["drop_from_prev"] > biggest_drop_n:
            biggest_drop_n = step["drop_from_prev"]
            biggest_drop_idx = i

    # Mark the biggest drop (if any users at all dropped off).
    for i, step in enumerate(funnel_steps):
        step["is_biggest_dropoff"] = (i == biggest_drop_idx and biggest_drop_n > 0)

    recent = sorted(
        real_users,
        key=lambda x: x.get("created_at") or 0,
        reverse=True,
    )[:10]

    return {
        "ok": True,
        # Iter 212m-3 — 5-step funnel: the canonical shape going
        # forward. `funnel.signed_up/connected_repo/shipped_task/paying`
        # below stays for backward-compat with the old AdminOverview
        # render path.
        "funnel_steps": funnel_steps,
        "biggest_dropoff_idx": biggest_drop_idx if biggest_drop_n > 0 else None,
        "funnel": {
            "signed_up":         n_signup,
            "connected_github":  n_github,
            "added_project":     n_proj,
            "sent_message":      n_sess,
            "shipped_code":      n_task,
            # Backward-compat aliases (Iter 196 schema).
            "connected_repo":    n_proj,
            "shipped_task":      n_task,
            "paying":            len(paying),
        },
        "conversion_rates": {
            # New canonical 5-step rates.
            "signup_to_github":  pct(n_github, n_signup),
            "github_to_project": pct(n_proj,   n_github),
            "project_to_message":pct(n_sess,   n_proj),
            "message_to_ship":   pct(n_task,   n_sess),
            # Legacy aliases (Iter 196).
            "signup_to_repo":    pct(n_proj,   n_signup),
            "repo_to_task":      pct(n_task,   n_proj),
            "task_to_paid":      pct(len(paying), n_task),
        },
        "totals": {
            "all_users":         len(all_users),
            "test_users_excluded": len(all_users) - len(real_users),
        },
        "recent_signups": [
            {
                "email":    u.get("email"),
                "tier":     u.get("tier", "free"),
                "has_repo": u.get("user_id") in project_uids,
                "has_task": u.get("user_id") in task_uids,
                "joined":   u.get("created_at"),
            }
            for u in recent
        ],
    }



# ─── Iter 212m — User-patterns insights ────────────────────────────────
#
# GET /admin/insights/user-patterns
#
# Aggregates the `ora_patterns` collection (populated fire-and-forget
# by `services/ora_learning.py::extract_session_patterns` after every
# chat turn) into a founder-readable snapshot:
#   • top 10 hot_files across all users (file → user count)
#   • top stack_signals by frequency (e.g. fastapi=12, react=9, ...)
#   • total users with patterns
#   • total sessions tracked
#
# Returns empty buckets when the collection is empty / absent (e.g.
# before the first session is mined) so the UI card never crashes.
@router.get("/insights/user-patterns")
async def user_patterns_insights(
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()

    from collections import Counter

    # Pull every pattern doc. The collection is small by design (1 doc
    # per (user_id, project_id) tuple) so a full scan is fine; can be
    # replaced with a server-side $unwind aggregation if it grows.
    docs = await db.ora_patterns.find(
        {},
        {"_id": 0, "user_id": 1, "hot_files": 1,
         "stack_signals": 1, "session_count": 1},
    ).to_list(5000)

    file_counter: Counter[str] = Counter()
    stack_counter: Counter[str] = Counter()
    user_ids: set[str] = set()
    total_sessions = 0

    for d in docs:
        uid = d.get("user_id")
        if uid:
            user_ids.add(uid)
        total_sessions += int(d.get("session_count") or 0)
        for f in (d.get("hot_files") or []):
            if isinstance(f, str) and f:
                file_counter[f] += 1
        for s in (d.get("stack_signals") or []):
            if isinstance(s, str) and s:
                stack_counter[s] += 1

    top_files = [
        {"file": f, "user_count": n}
        for f, n in file_counter.most_common(10)
    ]
    top_stack = [
        {"signal": s, "count": n}
        for s, n in stack_counter.most_common(20)
    ]

    return {
        "ok": True,
        "top_files":           top_files,
        "stack_distribution":  top_stack,
        "users_with_patterns": len(user_ids),
        "total_sessions":      total_sessions,
        "records":             len(docs),
    }




# ─────────────────────────────────────────────────────────────────────
# Iter 212h — Production Error Reporting
#
# Frontend silently posts every console.error / unhandledrejection to
# /errors/report. Admins see them in a dedicated tab and can either
# resolve manually or hand off to ORA for auto-fix via chat_with_tools.
#
# Dedupes by (message + url) so a console error firing 4000 times
# doesn't bloat the collection — count is incremented in place.
# ─────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone


class ErrorReport(BaseModel):
    message:   str
    stack:     str = ""
    url:       str = ""
    timestamp: str = ""
    type:      str = "console_error"


@router.post("/errors/report")
async def report_error(body: ErrorReport, request: Request) -> dict:
    """Public — no auth. Frontend posts every console.error here.
    Dedupes by (message, url); increments `count` on repeat.
    """
    db = get_db()
    if db is None:
        return {"ok": False, "error": "db_unavailable"}
    now = datetime.now(timezone.utc)
    # Trim absurdly long stack traces so a single chatty bug can't blow
    # up a document past Mongo's 16 MB cap.
    msg   = (body.message or "")[:4_000]
    stack = (body.stack   or "")[:16_000]
    url   = (body.url     or "")[:1_000]
    if not msg.strip():
        return {"ok": False, "error": "empty_message"}
    update = {
        "$inc": {"count": 1},
        "$set": {
            "last_seen":  now.isoformat(),
            "stack":      stack,
            "type":       body.type or "console_error",
            "user_agent": (request.headers.get("user-agent") or "")[:500],
        },
        "$setOnInsert": {
            "message":    msg,
            "url":        url,
            "first_seen": now.isoformat(),
            "resolved":   False,
            "autofix_status": "idle",
        },
    }
    await db.frontend_errors.update_one(
        {"message": msg, "url": url},
        update,
        upsert=True,
    )
    return {"ok": True}


@router.get("/errors")
async def list_errors(
    authorization: Optional[str] = Header(None),
    include_resolved: bool = False,
    limit: int = 200,
) -> dict:
    """Admin only — list errors sorted by count desc."""
    await _require_admin(authorization)
    db = require_db()
    q: dict = {} if include_resolved else {"resolved": {"$ne": True}}
    cursor = (db.frontend_errors.find(q)
                                 .sort("count", -1)
                                 .limit(min(max(limit, 1), 500)))
    items = []
    async for d in cursor:
        items.append({
            "id":            str(d.get("_id")),
            "message":       d.get("message", ""),
            "stack":         d.get("stack", "")[:2_000],
            "url":           d.get("url", ""),
            "type":          d.get("type", "console_error"),
            "count":         int(d.get("count", 0)),
            "first_seen":    d.get("first_seen", ""),
            "last_seen":     d.get("last_seen", ""),
            "resolved":      bool(d.get("resolved", False)),
            "autofix_status": d.get("autofix_status", "idle"),
            "user_agent":    (d.get("user_agent") or "")[:200],
        })
    return {"ok": True, "errors": items, "total": len(items)}


@router.post("/errors/{error_id}/autofix")
async def autofix_error(
    error_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Admin only — kick ORA off to investigate + fix the error.

    Fires `chat_with_tools` in the background so this endpoint returns
    fast. Status moves `idle → queued → done|failed` on the error doc.
    """
    admin = await _require_admin(authorization)
    db = require_db()
    from bson import ObjectId
    try:
        oid = ObjectId(error_id)
    except Exception as _bie:
        raise HTTPException(status_code=400, detail="bad_error_id") from _bie

    doc = await db.frontend_errors.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="error_not_found")

    await db.frontend_errors.update_one(
        {"_id": oid},
        {"$set": {"autofix_status": "queued",
                  "autofix_started": datetime.now(timezone.utc).isoformat()}},
    )

    msg   = doc.get("message", "")
    stack = doc.get("stack", "")[:3_000]
    url   = doc.get("url", "")
    prompt = (
        "A user-facing JS error is firing in production:\n\n"
        f"  message: {msg}\n"
        f"  url:     {url}\n\n"
        f"```\n{stack}\n```\n\n"
        "Investigate the root cause and ship a fix. Read the relevant "
        "files first, then make the change."
    )

    async def _run_autofix() -> None:
        try:
            from services.orchestrator import chat_with_tools
            result = await chat_with_tools(
                prompt=prompt,
                user_id=admin.get("user_id"),
                project_id=None,
                history_lines=[],
                live_invocations_ref=None,
                mode="maxx",
            )
            ok = bool(result and result.get("ok"))
            await db.frontend_errors.update_one(
                {"_id": oid},
                {"$set": {
                    "autofix_status": "done" if ok else "failed",
                    "autofix_finished": datetime.now(timezone.utc).isoformat(),
                    "autofix_response": (result.get("content") or "")[:5_000]
                                          if isinstance(result, dict) else "",
                }},
            )
        except Exception as e:                       # noqa: BLE001
            logging.getLogger("admin").warning(
                "autofix_error %s failed: %r", error_id, e,
            )
            await db.frontend_errors.update_one(
                {"_id": oid},
                {"$set": {"autofix_status": "failed",
                          "autofix_error": str(e)[:1_000]}},
            )

    asyncio.create_task(_run_autofix())
    return {"ok": True, "queued": True, "error_id": error_id}


@router.post("/errors/{error_id}/resolve")
async def resolve_error(
    error_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Admin only — mark this error as resolved (hides it from the list)."""
    await _require_admin(authorization)
    db = require_db()
    from bson import ObjectId
    try:
        oid = ObjectId(error_id)
    except Exception as _bie:
        raise HTTPException(status_code=400, detail="bad_error_id") from _bie
    r = await db.frontend_errors.update_one(
        {"_id": oid},
        {"$set": {"resolved": True,
                  "resolved_at": datetime.now(timezone.utc).isoformat()}},
    )
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="error_not_found")
    return {"ok": True, "resolved": True}



# ── Iter 212m-24 — House Rules (admin-defined ORA prompt) ───────────
# A single global system-prompt-style block that ORA reads BEFORE its
# own persona / tool catalog / project context, with individual
# green/red toggles per target (chat, advisor) and per mode (swift,
# pro, maxx). See services/house_rules.py for storage + injection.

class HouseRulesPayload(BaseModel):
    prompt:           str = ""
    enabled_chat:     bool = False
    enabled_advisor:  bool = False
    enabled_swift:    bool = False
    enabled_pro:      bool = False
    enabled_maxx:     bool = False


@router.get("/house-rules")
async def admin_house_rules_read(authorization: Optional[str] = Header(None)):
    """Return the current house-rules doc. Admin-only."""
    await _require_admin(authorization)
    from services.house_rules import get_house_rules_doc
    doc = await get_house_rules_doc()
    # Mongo's _id is fine to return as the literal "singleton" string.
    # datetime → iso for JSON.
    ua = doc.get("updated_at")
    if hasattr(ua, "isoformat"):
        doc = {**doc, "updated_at": ua.isoformat()}
    return doc


@router.put("/house-rules")
async def admin_house_rules_write(
    payload: HouseRulesPayload,
    authorization: Optional[str] = Header(None),
):
    """Persist the house-rules doc. Admin-only.

    Validates and writes the singleton document; invalidates the
    in-process cache so the next chat turn picks up the new rules.
    """
    admin = await _require_admin(authorization)
    from services.house_rules import set_house_rules_doc
    try:
        doc = await set_house_rules_doc(
            payload.model_dump(), by_user_id=admin.get("user_id") or "",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True, "house_rules": doc}
