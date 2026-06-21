"""
aurem_cto.routers.unlock — Time-windowed GitHub-collaborator access.

Customer files a request for temporary collaborator access on their
own repo. The request is persisted with status="pending" and reviewed
manually by an admin (no automated approval yet). The `GET /mine`
endpoint flags requests older than 7 days as `stale=True` so the UI
can prompt the user to email support rather than silently waiting.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import require_db

router = APIRouter(prefix="/unlock", tags=["AUREM CTO Unlock"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UnlockRequestBody(BaseModel):
    reason: str = Field(..., min_length=10, max_length=2000)


@router.post("/request")
async def request_unlock(body: UnlockRequestBody,
                          authorization: str = Header(None)) -> dict[str, Any]:
    me = await current_dev(authorization)
    db = require_db()
    import uuid as _u
    req_id = _u.uuid4().hex[:16]
    await db.aurem_cto_unlock_requests.insert_one({
        "request_id":  req_id,
        "user_id":     me["user_id"],
        "email":       me.get("email", ""),
        "reason":      body.reason,
        "status":      "pending",
        "requested_at": _now_iso(),
        "created_at":  time.time(),
        "approved_at": None,
        "approved_by": None,
        "expires_at":  None,
        "revoked_at":  None,
    })
    return {
        "ok":         True,
        "request_id": req_id,
        "status":     "pending",
        "message": (
            "Your unlock request has been submitted. Our team reviews "
            "these manually — email polarisbuiltinc@gmail.com if you need "
            "this expedited."
        ),
    }


_STALE_AFTER_S = 7 * 86400


@router.get("/mine")
async def my_requests(authorization: str = Header(None)) -> dict[str, Any]:
    me = await current_dev(authorization)
    db = require_db()
    cur = db.aurem_cto_unlock_requests.find(
        {"user_id": me["user_id"]}, {"_id": 0},
    ).sort("requested_at", -1).limit(20)
    rows = []
    now = time.time()
    async for d in cur:
        if d.get("status") == "pending":
            # Older docs predate the created_at field — parse requested_at
            # ISO string as a fallback so the staleness check still works
            # for legacy rows.
            ts = d.get("created_at")
            if ts is None:
                try:
                    ts = datetime.fromisoformat(
                        d.get("requested_at", "").replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    ts = now
            d["stale"] = (now - ts) > _STALE_AFTER_S
        else:
            d["stale"] = False
        rows.append(d)
    return {"requests": rows}
