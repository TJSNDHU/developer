"""
services/subscription_tiers.py — single source of truth for plan limits.

Every gate in the codebase (task counter, mode access, parallel agents,
priority queue) MUST read from here.  Anywhere else duplicating the
numbers is a bug.

Iter 153 — added per-tier `modes` list and `allowed_modes_for_tier()`
helper.  Per-tier task caps were lowered from "unlimited" to capped
values so we don't bleed cash on power users (Pro 300/mo, Team 400/mo).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Union


class Tier(str, Enum):
    FREE    = "free"
    STARTER = "starter"
    PRO     = "pro"
    TEAM    = "team"
    FOUNDER = "founder"   # internal — never billed; mirrors Team features


TIER_LIMITS: dict[Tier, dict] = {
    Tier.FREE: {
        "tasks_per_month":     10,
        "modes":               ["swift"],
        "maxx_mode":           False,
        "brain_memory":        False,
        "parallel_agents":     False,
        "priority_queue":      False,
        "price_monthly":       0,
    },
    Tier.STARTER: {
        "tasks_per_month":     50,
        "modes":               ["swift"],
        "maxx_mode":           False,
        "brain_memory":        True,
        "parallel_agents":     False,
        "priority_queue":      False,
        "price_monthly":       9,
    },
    Tier.PRO: {
        "tasks_per_month":     300,
        "modes":               ["swift", "pro"],
        "maxx_mode":           False,
        "brain_memory":        True,
        "parallel_agents":     True,
        "priority_queue":      False,
        "price_monthly":       19,
    },
    Tier.TEAM: {
        "tasks_per_month":     400,
        "modes":               ["swift", "pro", "maxx"],
        "maxx_mode":           True,
        "brain_memory":        True,
        "parallel_agents":     True,
        "priority_queue":      True,
        "price_monthly":       49,
    },
    Tier.FOUNDER: {
        "tasks_per_month":     None,
        "modes":               ["swift", "pro", "maxx"],
        "maxx_mode":           True,
        "brain_memory":        True,
        "parallel_agents":     True,
        "priority_queue":      True,
        "price_monthly":       0,
    },
}


def _coerce(tier: Optional[str]) -> Tier:
    """Normalise any string to a Tier — unknown values fall back to FREE."""
    if not tier:
        return Tier.FREE
    try:
        return Tier(tier)
    except ValueError:
        return Tier.FREE


def get_limit(tier: Optional[str], feature: str) -> Union[int, bool, None]:
    """Return the raw feature value (int, bool, None=unlimited)."""
    return TIER_LIMITS[_coerce(tier)].get(feature)


def can_use_feature(tier: Optional[str], feature: str) -> bool:
    """True if the tier is allowed to use the feature."""
    val = TIER_LIMITS[_coerce(tier)].get(feature)
    if val is None:
        return True
    if isinstance(val, bool):
        return val
    return True


def plan_price(tier: Optional[str]) -> int:
    """USD/month price for the tier — used by Settings + Landing."""
    return TIER_LIMITS[_coerce(tier)]["price_monthly"]


def allowed_modes_for_tier(tier: Optional[str]) -> list:
    """Iter 153 — list of mode keys the tier may select in the UI.

    Used by `chat_with_tools(mode=…)` to clamp any user-supplied mode
    to what their plan covers (last entry is treated as their best
    available fallback).
    """
    t = _coerce(tier)
    return TIER_LIMITS.get(t, TIER_LIMITS[Tier.FREE]).get("modes", ["swift"])
