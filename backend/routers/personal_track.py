"""
routers/personal_track.py — Iter 212m-237

Lightweight user-facing endpoints for the Personal Track discovery
surface. Kept separate from `scaffold.py` (which owns the heavy
build/deploy pipeline) because these are pure activation-metric
writes with no draft/repo/deploy dependencies.

Endpoints:
    POST /api/aurem-dev/personal-track/legacy-nudge-click
        Fired by the Dashboard `PersonalTrackBanner` when a legacy
        user (dev_users.track === null) clicks "Try it". Persists
        `personal_nudge_clicked_at` on the caller's dev_users row.
        Idempotent — first click wins, subsequent clicks are no-ops
        (we care about *whether* they clicked, not how many times).

The endpoint intentionally does NOT set `track` — that still requires
the user to explicitly pick on /choose-track. This event is purely
an interest signal.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Header, HTTPException
from typing import Optional

from cto_services.auth import current_dev
from cto_services.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/personal-track", tags=["Personal Track — Activation"])


@router.post("/legacy-nudge-click")
async def legacy_nudge_click(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Record the first time a legacy user clicks the dashboard nudge.

    Idempotent via `$setOnInsert`-style logic: if the field already
    exists we don't overwrite it, so the returned `first_click` flag
    tells the frontend whether this was the first-ever click.
    """
    payload = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    user_id = payload["user_id"]
    now = time.time()

    existing = await db.dev_users.find_one(
        {"user_id": user_id},
        {"personal_nudge_clicked_at": 1},
    )
    if existing and existing.get("personal_nudge_clicked_at"):
        return {
            "ok": True,
            "first_click": False,
            "clicked_at": existing["personal_nudge_clicked_at"],
        }

    await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {"personal_nudge_clicked_at": now}},
    )
    return {"ok": True, "first_click": True, "clicked_at": now}
