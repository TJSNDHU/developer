"""
services/usage.py — Token usage aggregation + plan-limit enforcement.

Single source of truth for "how much has this user burned" and
"what's their effective ceiling".

  effective_limit = PLAN_LIMITS[user.tier]  +  user.tokens_granted
  used            = sum(cto_tasks.tokens_used where user_id=X, status=done)
  remaining       = effective_limit - used
"""
from __future__ import annotations
from typing import Optional
from fastapi import HTTPException

from cto_services.db import require_db

PLAN_LIMITS = {"free": 1000, "pro": 50000, "team": 100000}


async def get_usage(user_id: str) -> dict:
    db = require_db()
    user = await db.dev_users.find_one(
        {"user_id": user_id},
        {"tier": 1, "tokens_granted": 1},
    )
    if not user:
        raise HTTPException(404, "User not found")
    tier = user.get("tier", "free")
    granted = int(user.get("tokens_granted") or 0)
    plan_limit = PLAN_LIMITS.get(tier, PLAN_LIMITS["free"])
    effective = plan_limit + granted

    agg = await db.cto_tasks.aggregate([
        {"$match": {"user_id": user_id, "status": "done"}},
        {"$group": {"_id": None, "total": {"$sum": "$tokens_used"}}},
    ]).to_list(1)
    used = int(agg[0]["total"]) if agg else 0
    remaining = max(0, effective - used)
    pct = round((used / effective) * 100, 1) if effective > 0 else 0

    return {
        "user_id": user_id,
        "tier": tier,
        "plan_limit": plan_limit,
        "tokens_granted": granted,
        "effective_limit": effective,
        "used": used,
        "remaining": remaining,
        "pct_used": pct,
        "is_exhausted": used >= effective,
    }


async def assert_has_budget(user_id: str) -> None:
    """Raises HTTP 402 if the user is out of tokens."""
    u = await get_usage(user_id)
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
