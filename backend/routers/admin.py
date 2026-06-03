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
    # Iter 64 — expand probed services to match the real surface area.
    # Probes are best-effort; any 5xx / network error → unreachable.
    probe_targets = [
        ("GitHub API",        "https://api.github.com"),
        ("OpenRouter",        "https://openrouter.ai/api/v1/models"),
        ("Cloudflare API",    "https://api.cloudflare.com/client/v4/user/tokens/verify"),
        ("Vercel API",        "https://api.vercel.com/v2/user"),
        ("Anthropic API",     "https://api.anthropic.com/v1/messages"),
        ("Sentry ingest",     "https://sentry.io/api/0/"),
        ("Stripe API",        "https://api.stripe.com/v1/"),
    ]
    for name, url in probe_targets:
        try:
            t0 = time.time()
            async with httpx.AsyncClient(timeout=4.0) as c:
                # HEAD/GET unauth ping — we only care if the host is reachable
                r = await c.get(url, headers={"User-Agent": "AUREM-arch-probe/1.0"})
            # 2xx/3xx/4xx all mean "host alive" — 5xx is degraded.
            if r.status_code < 500:
                services[name] = {
                    "status": "live",
                    "latency_ms": round((time.time() - t0) * 1000),
                }
            else:
                services[name] = {
                    "status": "degraded",
                    "latency_ms": round((time.time() - t0) * 1000),
                    "note": f"HTTP {r.status_code}",
                }
        except Exception as e:
            services[name] = {
                "status": "unreachable", "latency_ms": 0,
                "note": str(e)[:80],
            }

    integrations = {
        "openrouter (deepseek)":    bool(os.getenv("OPENROUTER_API_KEY")),
        "emergent_llm (maxx)":      bool(os.getenv("EMERGENT_LLM_KEY")),
        "anthropic (claude maxx)":  bool(os.getenv("ANTHROPIC_API_KEY")),
        "github_oauth":             bool(os.getenv("GITHUB_OAUTH_CLIENT_ID")),
        "github_oauth_secret":      bool(os.getenv("GITHUB_OAUTH_CLIENT_SECRET")),
        "cloudflare_purge":         bool(os.getenv("CLOUDFLARE_API_TOKEN") and
                                         os.getenv("CLOUDFLARE_ZONE_ID")),
        "vercel_deploy_hook":       bool(os.getenv("VERCEL_API_TOKEN")),
        "sentry_dsn":               bool(os.getenv("SENTRY_DSN")),
        "stripe":                   bool(os.getenv("STRIPE_SECRET_KEY")),
        "mongodb":                  db is not None,
        "resend (email)":           bool(os.getenv("RESEND_API_KEY")),
    }

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
