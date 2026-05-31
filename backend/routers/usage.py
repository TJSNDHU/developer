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
from services.usage import get_usage

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
