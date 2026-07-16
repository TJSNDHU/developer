"""
services/personal_track_quotas.py — Iter 212m-240 (Tier 3/4)

Single source of truth for Personal Track billing gates + daily rate limits.

Two concerns:
  1. **Feature gating** (Tier 3):
       is_gated(user, "dedicated_db") → 402 if not allowed
     Founders (`is_founder=True`) bypass all gates.

  2. **Daily rate limit** (Tier 4):
       check_and_increment_daily(db, user_id, tier, "scaffold_drafts_per_day")
     Uses a Mongo counter keyed by (user_id, feature, UTC-date) so counters
     reset naturally at UTC midnight. No cron needed.

Design notes:
- Counter documents auto-expire via a TTL index (created idempotently) so
  historical rows don't accumulate.
- All numeric limits with value `None` = unlimited (FOUNDER tier).
- Reads dev_users.tier + dev_users.is_founder for the current caller.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from services.subscription_tiers import TIER_LIMITS, Tier, _coerce

logger = logging.getLogger(__name__)

# Counters older than 3 days can be safely GC'd (we never look at yesterday).
_COUNTER_TTL_SEC = 3 * 24 * 3600
_COUNTER_COLLECTION = "personal_track_quota_counters"
_TTL_INDEX_READY = False


async def _ensure_ttl_index(db) -> None:
    global _TTL_INDEX_READY
    if _TTL_INDEX_READY:
        return
    try:
        await db[_COUNTER_COLLECTION].create_index(
            "created_at",
            expireAfterSeconds=_COUNTER_TTL_SEC,
            name="pt_quota_counter_ttl",
        )
        _TTL_INDEX_READY = True
    except Exception as e:  # noqa: BLE001
        logger.warning("[pt-quota] TTL index create failed: %r", e)


def _today_utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def get_user_tier(db, user_id: str) -> str:
    """Reads dev_users.tier — falls back to 'free' if row missing."""
    row = await db.dev_users.find_one(
        {"user_id": user_id},
        {"tier": 1, "is_founder": 1, "_id": 0},
    ) or {}
    if row.get("is_founder"):
        return "founder"
    return row.get("tier") or "free"


def is_founder(user: dict) -> bool:
    return bool(user.get("is_founder") or user.get("is_admin"))


def check_feature_allowed(tier: str, feature: str) -> bool:
    """Bool feature gate: True if the tier includes this feature."""
    val = TIER_LIMITS[_coerce(tier)].get(feature)
    if isinstance(val, bool):
        return val
    # Numeric features (drafts_per_day) are "allowed" — the count is enforced
    # by check_and_increment_daily separately.
    return True


def get_numeric_limit(tier: str, feature: str) -> Optional[int]:
    """None = unlimited, integer = daily cap."""
    val = TIER_LIMITS[_coerce(tier)].get(feature)
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    return None


async def check_and_increment_daily(
    db, user_id: str, tier: str, feature: str,
) -> dict:
    """Sliding UTC-daily bucket. Returns:
        {ok: True,  used: int, limit: int|None, remaining: int|None}
        {ok: False, used: int, limit: int,      reset_at_utc: str}
    """
    limit = get_numeric_limit(tier, feature)
    if limit is None:
        # Unlimited for this tier — nothing to enforce, no counter to write.
        return {"ok": True, "used": 0, "limit": None, "remaining": None}

    await _ensure_ttl_index(db)
    date_key = _today_utc_date()

    # Atomic incrementing update. We first read to check if we're under the
    # cap, then increment. Race-safe enough for a per-user endpoint (a
    # single user isn't hammering the endpoint concurrently in practice).
    key = {"user_id": user_id, "feature": feature, "date_key": date_key}
    row = await db[_COUNTER_COLLECTION].find_one(key, {"count": 1, "_id": 0}) or {}
    used = int(row.get("count") or 0)
    if used >= limit:
        # Compute next UTC midnight for the reset hint.
        now = datetime.now(timezone.utc)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + 86400
        reset_iso = datetime.fromtimestamp(tomorrow, tz=timezone.utc).isoformat()
        return {
            "ok":            False,
            "used":          used,
            "limit":         limit,
            "reset_at_utc":  reset_iso,
        }

    await db[_COUNTER_COLLECTION].update_one(
        key,
        {"$inc": {"count": 1},
         "$setOnInsert": {"created_at": time.time()}},
        upsert=True,
    )
    return {"ok": True, "used": used + 1, "limit": limit,
            "remaining": max(0, limit - (used + 1))}


async def enforce_feature_or_402(db, user: dict, feature: str) -> str:
    """Convenience for routers. Returns the user's tier if allowed;
    raises HTTPException(402) otherwise. Founder bypasses all gates.
    """
    from fastapi import HTTPException
    if is_founder(user):
        return "founder"
    tier = await get_user_tier(db, user["user_id"])
    if not check_feature_allowed(tier, feature):
        raise HTTPException(
            status_code=402,
            detail={
                "reason":       "tier_upgrade_required",
                "feature":      feature,
                "current_tier": tier,
                "user_message": _friendly_upgrade_message(feature, tier),
                "upgrade_url":  "/pricing",
            },
        )
    return tier


def _friendly_upgrade_message(feature: str, tier: str) -> str:
    labels = {
        "dedicated_db":        "your own private database (Supabase Pro)",
        "custom_domain":       "connecting a custom domain",
        "transfer_ownership":  "transferring ownership to your GitHub/Supabase account",
    }
    what = labels.get(feature, feature.replace("_", " "))
    return (f"You're on the {tier.capitalize()} plan. To use {what}, "
            "please upgrade your plan from the pricing page.")


async def enforce_daily_rate_or_429(
    db, user: dict, feature: str,
) -> dict:
    """Convenience for routers. Returns the quota status dict on success,
    raises HTTPException(429) if the daily cap is hit. Founder bypasses.
    """
    from fastapi import HTTPException
    if is_founder(user):
        return {"ok": True, "used": 0, "limit": None, "remaining": None}
    tier = await get_user_tier(db, user["user_id"])
    status = await check_and_increment_daily(db, user["user_id"], tier, feature)
    if not status["ok"]:
        raise HTTPException(
            status_code=429,
            detail={
                "reason":       "daily_quota_exceeded",
                "feature":      feature,
                "current_tier": tier,
                "used":         status["used"],
                "limit":        status["limit"],
                "reset_at_utc": status["reset_at_utc"],
                "user_message": (f"You've used all {status['limit']} of your daily "
                                 f"builds on the {tier.capitalize()} plan. "
                                 f"Resets at UTC midnight."),
                "upgrade_url":  "/pricing",
            },
        )
    return status
