"""
aurem_cto.routers.harden — Server hardening status (read-only).

Surfaces the most recent hardening report for the authenticated user.
The actual hardening run (SSH in, apt update/upgrade, install Docker +
Caddy, lock down root SSH, create deploy user) is a manual operation
right now — request it via support@auremcto.com. This endpoint just
serves the persisted report once that work is done.
"""
from __future__ import annotations

from fastapi import APIRouter, Header

from cto_services.auth import current_dev
from cto_services.db import require_db

router = APIRouter(prefix="/harden", tags=["AUREM CTO Hardening"])


@router.get("/last")
async def last_report(authorization: str = Header(None)) -> dict:
    """Returns the most recent hardening report for this user, or null."""
    me = await current_dev(authorization)
    db = require_db()
    row = await db.aurem_cto_server_hardenings.find_one(
        {"user_id": me["user_id"]},
        {"_id": 0},
        sort=[("ts", -1)],
    )
    return {"report": row}
