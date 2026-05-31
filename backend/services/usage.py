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
from fastapi import HTTPException

from cto_services.db import require_db

PLAN_LIMITS = {
    "free":    1_000,
    "pro":     50_000,
    "team":    100_000,
    # Founder plan — practically unlimited. The check in `assert_has_budget`
    # short-circuits before this value is ever compared, but we keep a huge
    # sentinel so any code path that reads `effective_limit` does the right
    # thing (e.g. UI shows "∞" / huge number).
    "founder": 1_000_000_000,
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
    }


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
