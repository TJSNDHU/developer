"""
services/subscription_tiers.py — single source of truth for plan limits.

Every gate in the codebase (task counter, maxx-mode, parallel agents,
priority queue) MUST read from here.  Anywhere else duplicating the
numbers is a bug.

Tiers are str-enum so they survive Mongo serialization without an extra
codec, and `get_limit` / `can_use_feature` safe-default to `Tier.FREE`
on any unknown string (so stale rows never crash).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Union


class Tier(str, Enum):
    FREE    = "free"
    STARTER = "starter"
    PRO     = "pro"
    TEAM    = "team"
    FOUNDER = "founder"   # internal — never billed; mirrors Pro features


TIER_LIMITS: dict[Tier, dict] = {
    Tier.FREE: {
        "tasks_per_month": 10,
        "maxx_mode":       False,
        "maxx_tasks_per_month": 0,    # no Maxx for free tier
        "brain_memory":    False,
        "parallel_agents": False,
        "priority_queue":  False,
        "price_monthly":   0,
    },
    Tier.STARTER: {
        "tasks_per_month": 50,
        "maxx_mode":       False,
        "maxx_tasks_per_month": 0,    # Starter has no Maxx access at all
        "brain_memory":    True,
        "parallel_agents": False,
        "priority_queue":  False,
        "price_monthly":   9,
    },
    Tier.PRO: {
        "tasks_per_month": None,    # unlimited
        "maxx_mode":       True,
        "maxx_tasks_per_month": 100,  # hard cap — after this, code/review fall back to DeepSeek
        "brain_memory":    True,
        "parallel_agents": True,
        "priority_queue":  False,
        "price_monthly":   19,
    },
    Tier.TEAM: {
        "tasks_per_month": None,
        "maxx_mode":       True,
        "maxx_tasks_per_month": None,  # unlimited Maxx for paying Team tier
        "brain_memory":    True,
        "parallel_agents": True,
        "priority_queue":  True,
        "price_monthly":   49,
    },
    # Internal account — never billed, mirrors Pro features so founders
    # don't get gated out of Maxx / parallel agents during eat-our-own-
    # dogfood testing.
    Tier.FOUNDER: {
        "tasks_per_month": None,
        "maxx_mode":       True,
        "maxx_tasks_per_month": None,  # unlimited for internal use
        "brain_memory":    True,
        "parallel_agents": True,
        "priority_queue":  True,
        "price_monthly":   0,
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
    """True if the tier is allowed to use the feature.

    - Bool features (maxx_mode, brain_memory…) return their stored bool.
    - Numeric features (tasks_per_month) return True iff the tier owns
      *any* quota; the actual count is checked elsewhere via get_limit.
    - `None` means unlimited → always True.
    """
    val = TIER_LIMITS[_coerce(tier)].get(feature)
    if val is None:
        return True
    if isinstance(val, bool):
        return val
    return True


def plan_price(tier: Optional[str]) -> int:
    """USD/month price for the tier — used by Settings + Landing."""
    return TIER_LIMITS[_coerce(tier)]["price_monthly"]
