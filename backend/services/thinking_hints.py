"""
services/thinking_hints.py — Iter 158

Tier-aware "thinking hints" shown to the user during chat-busy states.
The hint slides in next to the spinner and converts what used to be
dead wait time into a small upsell / feature-discovery moment.

Design goals
────────────
1. **No 3rd-party ad networks.** Kickbacks.ai-style ads were rejected to
   protect AUREM's premium positioning. All copy is first-party,
   directs revenue back to AUREM via upsells or feature engagement.

2. **Tier-aware.** Free users see Starter/Pro upsells. Paid users see
   feature highlights or one tier up. Founder tier sees nothing.

3. **Admin-managed.** Hints live in Mongo (collection
   `thinking_hints`) and are fully CRUD-able from the admin panel
   without redeploys. A seed payload runs once at first import.

4. **Cheap to serve.** A single random pick per call, weighted by
   `weight` (default 10). Cached in memory for 60s so a thinking-heavy
   chat session doesn't hammer Mongo.

Document shape
──────────────
    {
        "_id": ObjectId(...),
        "hint_id":     "free_upgrade_starter_v1",   # human-stable slug
        "tier":        "free",                       # match user tier
        "headline":    "10 tasks done already?",
        "body":        "Starter unlocks 50 tasks + Project Brain.",
        "cta_text":    "Upgrade — $9/mo",
        "cta_link":    "/settings#billing",
        "emoji":       "🚀",                         # optional, 1-2 chars
        "active":      true,
        "weight":      10,                           # 1-100, higher = more
        "created_at":  1.728e9,
        "updated_at":  1.728e9,
    }
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Re-pull from Mongo every 60s so admin edits are felt without a restart
# but we still cache aggressively during chat-storm minutes.
_CACHE_TTL_S = 60.0
_cache: dict[str, Any] = {"ts": 0.0, "by_tier": {}}

# ── Default seed hints ────────────────────────────────────────────────
# Loaded once at app boot via ensure_default_hints(). Admin edits in the
# DB are NEVER overwritten — we only insert if `hint_id` is missing.
_DEFAULT_HINTS: list[dict] = [
    # ── FREE tier (heaviest upsell pressure) ─────────────────────────
    {
        "hint_id": "free_unlock_starter",
        "tier": "free",
        "emoji": "💎",
        "headline": "Loving AUREM? Unlock more.",
        "body": "Starter $9/mo — 50 tasks + Project Brain memory.",
        "cta_text": "Upgrade in 30s",
        "cta_link": "stripe:starter",
        "weight": 25,
    },
    {
        "hint_id": "free_swift_speed",
        "tier": "free",
        "emoji": "⚡",
        "headline": "Pro mode is 2× more thorough.",
        "body": "Pro $19/mo runs a diff review pass before commit.",
        "cta_text": "See Pro features",
        "cta_link": "/pricing",
        "weight": 15,
    },
    {
        "hint_id": "free_maxx_teaser",
        "tier": "free",
        "emoji": "🚀",
        "headline": "Team plan ships with Claude Sonnet watchdog.",
        "body": "Two AI engineers per task. $49/user, 400 tasks.",
        "cta_text": "Compare plans",
        "cta_link": "/pricing",
        "weight": 10,
    },
    {
        "hint_id": "free_brain_memory",
        "tier": "free",
        "emoji": "🧠",
        "headline": "Project Brain remembers your stack.",
        "body": "Conventions, recent commits — no re-explaining. From $9.",
        "cta_text": "Unlock memory",
        "cta_link": "/pricing",
        "weight": 12,
    },

    # ── STARTER tier (push to Pro) ───────────────────────────────────
    {
        "hint_id": "starter_pro_diff",
        "tier": "starter",
        "emoji": "🔍",
        "headline": "Pro catches 30% more bugs.",
        "body": "Diff-review mode + 300 tasks/month, just $19.",
        "cta_text": "Try Pro",
        "cta_link": "stripe:pro",
        "weight": 25,
    },
    {
        "hint_id": "starter_parallel",
        "tier": "starter",
        "emoji": "⚙️",
        "headline": "Parallel agents = ship 3× faster.",
        "body": "Backend / frontend / tests in one go — Pro plan.",
        "cta_text": "See Pro perks",
        "cta_link": "/pricing",
        "weight": 15,
    },
    {
        "hint_id": "starter_automations",
        "tier": "starter",
        "emoji": "🔁",
        "headline": "Automations turn chats into workflows.",
        "body": "Webhook-triggered ship-on-push — Pro feature.",
        "cta_text": "Upgrade to Pro",
        "cta_link": "/settings#billing",
        "weight": 10,
    },

    # ── PRO tier (cross-sell Maxx / Team) ────────────────────────────
    {
        "hint_id": "pro_maxx_unlock",
        "tier": "pro",
        "emoji": "🚀",
        "headline": "Maxx mode = Claude reviews every commit.",
        "body": "Team plan adds Maxx + priority queue. $49/user.",
        "cta_text": "Try Team",
        "cta_link": "stripe:team",
        "weight": 25,
    },
    {
        "hint_id": "pro_priority_queue",
        "tier": "pro",
        "emoji": "⏱️",
        "headline": "Skip the queue on Team plan.",
        "body": "Sub-3s response even during peak load.",
        "cta_text": "Upgrade to Team",
        "cta_link": "/settings#billing",
        "weight": 12,
    },
    {
        "hint_id": "pro_team_admin",
        "tier": "pro",
        "emoji": "👥",
        "headline": "Ship as a squad.",
        "body": "Team plan = admin dashboard + role-based access.",
        "cta_text": "See Team",
        "cta_link": "/pricing",
        "weight": 10,
    },

    # ── TEAM tier (loyalty + feature discovery) ──────────────────────
    {
        "hint_id": "team_ask_ora",
        "tier": "team",
        "emoji": "💡",
        "headline": "Stuck on a decision? Ask ORA.",
        "body": "Right-side panel — second opinion in one click.",
        "cta_text": "Try ASK ORA",
        "cta_link": "",
        "weight": 18,
    },
    {
        "hint_id": "team_vanguard",
        "tier": "team",
        "emoji": "🛡️",
        "headline": "Vanguard 007 scans 25+ secret patterns.",
        "body": "AWS, Stripe, GitHub keys — caught before commit.",
        "cta_text": "View scan logs",
        "cta_link": "/admin/vanguard",
        "weight": 12,
    },
    {
        "hint_id": "team_refer_friend",
        "tier": "team",
        "emoji": "🎁",
        "headline": "Refer a dev, both get a month free.",
        "body": "Your referral link is in Settings → Refer.",
        "cta_text": "Share link",
        "cta_link": "/settings#referral",
        "weight": 10,
    },

    # ── FOUNDER tier — feature highlights only, no upsells ───────────
    {
        "hint_id": "founder_pulse",
        "tier": "founder",
        "emoji": "📈",
        "headline": "Check the daily pulse.",
        "body": "Admin → Overview shows ship rate, MRR, errors.",
        "cta_text": "Open admin",
        "cta_link": "/admin",
        "weight": 10,
    },
]


def _now() -> float:
    return time.time()


async def ensure_default_hints(db) -> int:
    """Idempotent seed. Inserts any default hint whose `hint_id` is
    missing from the collection. Existing rows are NEVER updated —
    admin edits stay in place. Returns count inserted."""
    if db is None:
        return 0
    existing = await db.thinking_hints.distinct("hint_id")
    seen = set(existing or [])
    to_insert = []
    now = _now()
    for h in _DEFAULT_HINTS:
        if h["hint_id"] in seen:
            continue
        doc = dict(h)
        doc.setdefault("active", True)
        doc["created_at"] = now
        doc["updated_at"] = now
        to_insert.append(doc)
    if to_insert:
        await db.thinking_hints.insert_many(to_insert)
        # Bust the cache so the new rows can be served immediately.
        _cache["ts"] = 0.0
        logger.info("thinking_hints seeded: %d new entries", len(to_insert))
    return len(to_insert)


async def _refresh_cache(db) -> dict[str, list[dict]]:
    """Pull active hints grouped by tier. Cached for 60s."""
    if _now() - _cache["ts"] < _CACHE_TTL_S and _cache["by_tier"]:
        return _cache["by_tier"]
    by_tier: dict[str, list[dict]] = {}
    if db is None:
        _cache.update(ts=_now(), by_tier=by_tier)
        return by_tier
    cursor = db.thinking_hints.find(
        {"active": True},
        {"_id": 0, "hint_id": 1, "tier": 1, "emoji": 1,
         "headline": 1, "body": 1, "cta_text": 1, "cta_link": 1,
         "weight": 1},
    )
    async for row in cursor:
        by_tier.setdefault(row.get("tier") or "free", []).append(row)
    _cache.update(ts=_now(), by_tier=by_tier)
    return by_tier


def _weighted_pick(items: list[dict]) -> Optional[dict]:
    if not items:
        return None
    weights = [max(1, int(it.get("weight") or 10)) for it in items]
    return random.choices(items, weights=weights, k=1)[0]


async def pick_hint(db, tier: str) -> Optional[dict]:
    """Pick a single random active hint for the given tier.

    Iter 160 — Founder tier is now hard-suppressed: the founder is
    the builder, not the buyer, so we never want admin/upsell strips
    leaking into the founder's own chat surface. Any unknown tier
    also falls through to None.
    """
    tier_norm = (tier or "free").lower().strip()
    if tier_norm == "founder":
        return None
    by_tier = await _refresh_cache(db)
    items = by_tier.get(tier_norm) or []
    pick = _weighted_pick(items)
    return pick


def bust_cache() -> None:
    """Call from admin-edit endpoints so the next pick reflects edits."""
    _cache["ts"] = 0.0
