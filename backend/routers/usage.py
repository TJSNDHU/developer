"""
routers/usage.py — User-facing token usage endpoint.

Reads aggregate token spend from `cto_tasks` and combines with the user's
plan limit + any admin-granted bonus tokens. Drives the chat warning banner.

Mounted under /api/aurem-dev/usage/* by main.py.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header

from cto_services.auth import current_dev
from services.usage import get_usage, get_maxx_usage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/usage", tags=["Usage"])


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
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return {"available": False}
    try:
        total = await db.ora_council_logs.count_documents({})
        code = await db.ora_council_logs.count_documents({"mode": "C"})
        corrections = await db.ora_council_logs.count_documents({"correction_applied": True})
        lint_blocks = await db.ora_council_logs.count_documents({"lint_blocked": True})
        tasks = await db.cto_tasks.count_documents({"status": "done"})
        users = await db.dev_users.count_documents({})
        return {
            "available": True,
            "users": users,
            "tasks_shipped": tasks,
            "interactions": total,
            "code_tasks": code,
            "claude_corrections": corrections,
            "correction_rate_pct": round((corrections / max(code, 1)) * 100, 1) if code else 0.0,
            "lint_blocks_caught": lint_blocks,
        }
    except Exception as e:
        logger.warning("public stats failed: %r", e)
        return {"available": False}
