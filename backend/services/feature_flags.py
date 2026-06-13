"""
services/feature_flags.py — Simple MongoDB-backed feature flags.

Iter 140 — central kill-switch + canary system. Replaces the ad-hoc
`os.getenv("FEATURE_X")` reads scattered across the codebase. Flags
live in the `feature_flags` collection:

    {
        "flag":            str,           # unique identifier
        "enabled":         bool,          # master switch
        "tier_allowlist":  list[str],     # [] = all tiers
        "user_allowlist":  list[str],     # explicit user_ids
        "description":     str,
    }

Usage at a call site:

    from services.feature_flags import is_enabled
    if await is_enabled("new_analytics_v2", user_id=uid, tier="pro"):
        # show new feature

A 60s process-local cache avoids hitting Mongo on every check; the
admin toggle endpoint invalidates by clearing `_cache` directly so
the next read repopulates.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory cache — refresh every 60s
_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 60.0


async def _load_flags() -> dict:
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is None:
            return _cache
        flags: dict = {}
        async for doc in db.feature_flags.find({}, {"_id": 0}):
            flags[doc["flag"]] = doc
        _cache = flags
        _cache_ts = now
        return flags
    except Exception as e:
        logger.warning("feature_flags load failed: %r", e)
        return _cache


async def is_enabled(
    flag: str,
    user_id: Optional[str] = None,
    tier: Optional[str] = None,
) -> bool:
    """Return True if the flag is enabled for this user/tier."""
    flags = await _load_flags()
    doc = flags.get(flag)
    if doc is None:
        return False
    if not doc.get("enabled", False):
        return False
    # User allowlist overrides tier check
    if user_id and user_id in (doc.get("user_allowlist") or []):
        return True
    # Tier allowlist — empty means all tiers
    tier_list = doc.get("tier_allowlist") or []
    if not tier_list:
        return True
    return (tier or "free") in tier_list


async def get_all_flags() -> list[dict]:
    """Return all flags (for admin UI)."""
    flags = await _load_flags()
    return list(flags.values())


def invalidate_cache() -> None:
    """Clear the in-memory cache so the next read refetches from Mongo.

    Called by admin endpoints after a toggle/create so the change
    propagates to this process within one request.
    """
    global _cache, _cache_ts
    _cache = {}
    _cache_ts = 0.0
