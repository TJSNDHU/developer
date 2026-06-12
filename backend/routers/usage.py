"""
routers/usage.py — User-facing token usage endpoint.

Reads aggregate token spend from `cto_tasks` and combines with the user's
plan limit + any admin-granted bonus tokens. Drives the chat warning banner.

Mounted under /api/aurem-dev/usage/* by main.py.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Header

from cto_services.auth import current_dev
from services.usage import get_usage, get_maxx_usage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/usage", tags=["Usage"])

# Iter 124j — /public/stats was polled by every landing-page visitor and
# ran 6 sequential Mongo count_documents() on each call. After ~42 min of
# sustained load this exhausted the Atlas connection pool / liveness probe
# budget and K8s killed the pod (production CrashLoopBackOff, 42-min cadence).
# Cache the response in-memory for 60 s and use the cheaper
# estimated_document_count() for unfiltered totals — counts don't need to
# be real-time on a marketing tile.
_PUBLIC_STATS_CACHE: dict = {"ts": 0.0, "data": None}
_PUBLIC_STATS_TTL_S = 60


@router.get("/me")
async def my_usage(authorization: Optional[str] = Header(None)):
    """Return the current user's token budget.

    Shape (consumed by `ChatPanel.jsx` warning banner):
      {
        user_id, tier, plan_limit, tokens_granted, effective_limit,
        used, remaining, pct_used, is_exhausted
      }
    """
    me = await current_dev(authorization)
    return await get_usage(me["user_id"])


# Iter 94 — Maxx-mode (Claude Sonnet) monthly counter, for the UI
# upgrade-nudge banner once the user has used > 75% of their cap.
@router.get("/maxx")
async def my_maxx_usage(authorization: Optional[str] = Header(None)):
    """Return the current user's Maxx-mode budget for this month.

    Shape:
      {
        tier, cap, used, remaining, capped
      }
    cap=None means unlimited (Team/Founder). cap=0 means tier has no
    Maxx access (Free/Starter).
    """
    me = await current_dev(authorization)
    return await get_maxx_usage(me["user_id"])



# ── Iter 45 — Public stats (no auth) ──
# Exposes correction_rate + total tasks shipped so auremcto.com landing
# page can render a live "Claude caught X% of mistakes" trust badge.
# NO PII — only aggregate counters from ora_council_logs.
@router.get("/public/stats")
async def public_stats():
    """Public marketing tile. 60-second in-memory cache to keep Atlas
    load O(1) regardless of visitor traffic."""
    now = time.time()
    if _PUBLIC_STATS_CACHE["data"] is not None and \
            now - _PUBLIC_STATS_CACHE["ts"] < _PUBLIC_STATS_TTL_S:
        return _PUBLIC_STATS_CACHE["data"]

    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return {"available": False}
    try:
        # estimated_document_count uses collection metadata — O(1) instead
        # of scanning. Acceptable trade-off for an unfiltered total on a
        # marketing tile.
        total = await db.ora_council_logs.estimated_document_count()
        users = await db.dev_users.estimated_document_count()
        # Filtered counts still need count_documents — but capped at 1
        # call per minute by the cache above.
        code = await db.ora_council_logs.count_documents({"mode": "C"})
        corrections = await db.ora_council_logs.count_documents({"correction_applied": True})
        lint_blocks = await db.ora_council_logs.count_documents({"lint_blocked": True})
        tasks = await db.cto_tasks.count_documents({"status": "done"})
        data = {
            "available": True,
            "users": users,
            "tasks_shipped": tasks,
            "interactions": total,
            "code_tasks": code,
            "claude_corrections": corrections,
            "correction_rate_pct": round((corrections / max(code, 1)) * 100, 1) if code else 0.0,
            "lint_blocks_caught": lint_blocks,
            "cached_at": int(now),
        }
        _PUBLIC_STATS_CACHE["data"] = data
        _PUBLIC_STATS_CACHE["ts"] = now
        return data
    except Exception as e:
        logger.warning("public stats failed: %r", e)
        # Serve stale cache if available — better than 500 for a marketing tile.
        if _PUBLIC_STATS_CACHE["data"] is not None:
            return _PUBLIC_STATS_CACHE["data"]
        return {"available": False}
