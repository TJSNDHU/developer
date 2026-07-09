"""
services/usage.py — Token usage aggregation + plan-limit enforcement.

Single source of truth for "how much has this user burned" and
"what's their effective ceiling".

  effective_limit = PLAN_LIMITS[user.tier]  +  user.tokens_granted
  used            = sum(cto_tasks.tokens_used where user_id=X, status=done)
  remaining       = effective_limit - used

FOUNDER TIER (Iter 30): users with `tier == "founder"` or `is_unlimited == True`
NEVER hit a token ceiling — every call to `assert_has_budget` short-circuits to
"OK", and the UI reports an infinite remaining balance via the `is_unlimited`
flag. This is for the company's own founder accounts (e.g. teji.ss1986@gmail.com)
so internal usage doesn't consume customer-facing quota.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from fastapi import HTTPException

from cto_services.db import require_db

PLAN_LIMITS = {
    "free":    1_000,
    "starter": 10_000,
    "pro":     50_000,
    "team":    100_000,
    # Founder plan — practically unlimited. The check in `assert_has_budget`
    # short-circuits before this value is ever compared, but we keep a huge
    # sentinel so any code path that reads `effective_limit` does the right
    # thing (e.g. UI shows "∞" / huge number).
    "founder": 1_000_000_000,
}

from services.subscription_tiers import get_limit as _tier_limit

# Monthly task-count limits (the headline pricing model — flat-fee, no
# token surprises).  Single source of truth lives in
# services/subscription_tiers.py; this thin shim keeps the old import
# path stable for everything that already imports MONTHLY_TASK_LIMITS.
MONTHLY_TASK_LIMITS = {
    "free":    _tier_limit("free",    "tasks_per_month"),
    "starter": _tier_limit("starter", "tasks_per_month"),
    "pro":     _tier_limit("pro",     "tasks_per_month"),
    "team":    _tier_limit("team",    "tasks_per_month"),
    "founder": _tier_limit("founder", "tasks_per_month"),
}

# ── Iter 94: Maxx-mode (Claude Sonnet 4.5) monthly cap per tier ────────
# Maxx mode is the expensive code/review path. Pro tier gets 100/mo to
# protect margin against power-user abuse (see FOUNDER_LAUNCH_CHECKLIST).
# Team / Founder are uncapped. Free / Starter have no Maxx at all.
MAXX_MONTHLY_LIMITS = {
    "free":    _tier_limit("free",    "maxx_tasks_per_month"),
    "starter": _tier_limit("starter", "maxx_tasks_per_month"),
    "pro":     _tier_limit("pro",     "maxx_tasks_per_month"),
    "team":    _tier_limit("team",    "maxx_tasks_per_month"),
    "founder": _tier_limit("founder", "maxx_tasks_per_month"),
}



# Founder allow-list: addresses here auto-promote to tier="founder" +
# is_admin=true on next login. Stored in env so we can hot-rotate without
# a deploy. Hardcoded fallback for the company founder so the system always
# recognises them even if env was forgotten in a redeploy.
_DEFAULT_FOUNDERS = {"teji.ss1986@gmail.com"}


def founder_emails() -> set[str]:
    raw = os.environ.get("FOUNDER_EMAILS", "")
    extra = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return _DEFAULT_FOUNDERS | extra


def is_founder_email(email: str | None) -> bool:
    return bool(email) and email.lower().strip() in founder_emails()


async def get_usage(user_id: str) -> dict:
    db = require_db()
    user = await db.dev_users.find_one(
        {"user_id": user_id},
        {"tier": 1, "tokens_granted": 1, "is_unlimited": 1, "email": 1},
    )
    if not user:
        raise HTTPException(404, "User not found")

    email = user.get("email")
    tier = user.get("tier", "free")
    # Defensive: email-based founder check wins over a stale tier value
    if is_founder_email(email) or tier == "founder" or user.get("is_unlimited"):
        tier = "founder"

    granted = int(user.get("tokens_granted") or 0)
    plan_limit = PLAN_LIMITS.get(tier, PLAN_LIMITS["free"])
    effective = plan_limit + granted

    agg = await db.cto_tasks.aggregate([
        {"$match": {"user_id": user_id, "status": "done"}},
        {"$group": {"_id": None, "total": {"$sum": "$tokens_used"}}},
    ]).to_list(1)
    used = int(agg[0]["total"]) if agg else 0

    # Founders are never exhausted — we still surface a usage number so
    # they can see their own burn, but `is_exhausted` stays False forever.
    is_unlimited = tier == "founder"
    remaining = max(0, effective - used) if not is_unlimited else 10**12
    pct = round((used / effective) * 100, 1) if (effective > 0 and not is_unlimited) else 0
    is_exhausted = False if is_unlimited else (used >= effective)

    # ── Monthly task counter (flat-fee meter). Counts every task this
    # user has SUBMITTED + ran since the first of the current UTC month.
    # Failed tasks are excluded (Iter 52 BUG 3) — a stale PAT or auth
    # error shouldn't burn the user's quota before the AI ran.
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0,
    )
    tasks_this_month = await db.cto_tasks.count_documents({
        "user_id": user_id,
        "created_at": {"$gte": month_start.timestamp()},
        "status": {"$in": ["done", "running", "pulling", "reading",
                            "fixing", "pushing", "queued"]},
    })
    # Iter 212m-190 — Developer-Tool scan fixes each count as 1 task
    # (see services/scan_fix_quota.py). Recorded per SUCCESSFUL fix.
    _sf = await db.scan_fix_usage.find_one(
        {"user_id": user_id,
         "month": f"{month_start.year:04d}-{month_start.month:02d}"},
        {"_id": 0, "count": 1},
    ) or {}
    tasks_this_month += int(_sf.get("count") or 0)
    task_cap = MONTHLY_TASK_LIMITS.get(tier, MONTHLY_TASK_LIMITS["free"])

    return {
        "user_id": user_id,
        "tier": tier,
        "plan_limit": plan_limit,
        "tokens_granted": granted,
        "effective_limit": effective,
        "used": used,
        "remaining": remaining,
        "pct_used": pct,
        "is_exhausted": is_exhausted,
        "is_unlimited": is_unlimited,
        "tasks_this_month": tasks_this_month,
        "monthly_task_cap": task_cap,
        "tasks_remaining":  None if task_cap is None
                            else max(0, task_cap - tasks_this_month),
    }


async def assert_has_task_budget(user_id: str) -> None:
    """Raise HTTP 402 if the user has hit their monthly task cap.

    Flat-fee tiers (free=10, starter=50) have a hard ceiling. Pro / Team /
    Founder are unlimited and short-circuit immediately.
    """
    u = await get_usage(user_id)
    if u.get("is_unlimited"):
        return
    cap = u.get("monthly_task_cap")
    if cap is None:
        return
    if u.get("tasks_this_month", 0) >= cap:
        raise HTTPException(402, detail={
            "error": "monthly_task_limit_reached",
            "tier": u["tier"],
            "tasks_this_month": u["tasks_this_month"],
            "monthly_task_cap": cap,
            "upgrade_url": "/settings#pricing",
            "message": (
                f"You've used all {cap} tasks on the {u['tier'].title()} "
                "plan this month. Upgrade to Pro for unlimited tasks."
            ),
        })


async def assert_has_budget(user_id: str) -> None:
    """Raises HTTP 402 if the user is out of tokens.

    Founders / unlimited accounts are always allowed through — this is the
    primary enforcement point for the no-token-burn mode.
    """
    u = await get_usage(user_id)
    if u.get("is_unlimited"):
        return  # Founder — never billed, never blocked.
    if u["is_exhausted"]:
        raise HTTPException(402, detail={
            "error": "token_limit_reached",
            "used": u["used"],
            "limit": u["effective_limit"],
            "upgrade_url": "/pricing",
            "message": (
                f"Token limit reached ({u['used']:,}/{u['effective_limit']:,}). "
                "Upgrade your plan or wait for an admin grant to continue."
            ),
        })


# ── Iter 94: Maxx-mode usage tracking ─────────────────────────────────
def _current_year_month() -> str:
    """Returns 'YYYY-MM' for current UTC time — the bucket key."""
    n = datetime.now(timezone.utc)
    return f"{n.year:04d}-{n.month:02d}"


async def get_maxx_usage(user_id: str) -> dict:
    """
    Return Maxx-mode (Claude) usage state for the current month.

    Output:
      tier             — user's tier
      cap              — int | None  (None = unlimited)
      used             — int (this month's Claude calls)
      remaining        — int | None
      capped           — bool (True iff used >= cap AND cap is not None)

    Free / Starter have cap=0 (never allowed). Pro=100. Team/Founder=None.
    """
    db = require_db()
    user = await db.dev_users.find_one(
        {"user_id": user_id},
        {"tier": 1, "email": 1, "is_unlimited": 1},
    )
    if not user:
        # Anonymous / unknown — treat as free.
        tier = "free"
    else:
        tier = user.get("tier", "free")
        if (is_founder_email(user.get("email"))
            or user.get("is_unlimited")
            or tier == "founder"):
            tier = "founder"

    cap = MAXX_MONTHLY_LIMITS.get(tier)
    # Unlimited (Team / Founder)
    if cap is None:
        return {"tier": tier, "cap": None, "used": 0,
                "remaining": None, "capped": False}

    bucket = _current_year_month()
    row = await db.cto_maxx_usage.find_one(
        {"user_id": user_id, "month": bucket},
        {"_id": 0, "count": 1, "overage_count": 1},
    ) or {}
    used = int(row.get("count") or 0)
    overage = int(row.get("overage_count") or 0)
    OVERAGE_PRICE = 0.50  # USD per Maxx task past cap (iter 101)
    return {
        "tier":             tier,
        "cap":              cap,
        "used":             used,
        "remaining":        max(0, cap - used),
        "capped":           used >= cap,
        "overage_count":    overage,
        "overage_cost_usd": round(overage * OVERAGE_PRICE, 2),
        "overage_price_usd": OVERAGE_PRICE,
    }


async def incr_maxx_usage(user_id: str) -> int:
    """Atomically bump this user's Maxx counter for the current month.
    Returns the new count. Iter 101 — also bumps `overage_count` when
    we're already past the cap (for end-of-month $0.50/task billing
    of Pro+ users)."""
    db = require_db()
    bucket = _current_year_month()
    # First, check if user is currently capped — that determines whether
    # this call is overage or included.
    pre = await get_maxx_usage(user_id)
    is_overage = bool(pre.get("capped"))
    inc = {"count": 1}
    if is_overage:
        inc["overage_count"] = 1
    res = await db.cto_maxx_usage.find_one_and_update(
        {"user_id": user_id, "month": bucket},
        {"$inc": inc,
         "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
        return_document=True,
    )
    return int((res or {}).get("count") or 1)

