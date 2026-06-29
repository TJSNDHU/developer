"""
routers/trust_level.py — Iter 212m-117

Per-user trust level for Loop Mode + Fix flows. Three levels mirroring
the Loop Engineering reference repo's L1/L2/L3 phased rollout:

  L1 — REPORT ONLY:
      Plan generates, user reviews. Execute / Verify / Scan / Ship are
      ALL skipped. Loop ends after plan approval as 'completed-l1'
      with the file diff suggested but never written.

  L2 — ASSISTED (DEFAULT — current behavior):
      Full pipeline runs, but Ship pauses with the manual Ship gate
      (iter 212m-111). User must click "Ship to GitHub" before any
      commit lands.

  L3 — UNATTENDED (AUTO-SHIP):
      Full pipeline runs and the manual Ship gate is BYPASSED — the
      engine commits straight away after Scan passes. Recommended
      only for high-trust users (founder defaults to L2 too — auto-
      ship is opt-in per user, not per role).

Per-finding Fix flows ALSO read this field: L1 → returns the proposed
patch without committing; L2/L3 → existing branch-per-fix behavior.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db

router = APIRouter(prefix="/api/aurem-dev/me", tags=["trust-level"])


VALID_LEVELS = {"L1", "L2", "L3"}
DEFAULT_LEVEL = "L2"


class TrustLevelBody(BaseModel):
    trust_level: str = Field(..., pattern=r"^L[123]$")


@router.get("/trust-level")
async def get_trust_level(
    authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True, "trust_level": DEFAULT_LEVEL,
                "source": "default_no_db"}
    row = await db.dev_users.find_one(
        {"user_id": me["user_id"]}, {"_id": 0, "trust_level": 1},
    )
    level = (row or {}).get("trust_level") or DEFAULT_LEVEL
    if level not in VALID_LEVELS:
        level = DEFAULT_LEVEL
    return {"ok": True, "trust_level": level, "default": DEFAULT_LEVEL}


@router.put("/trust-level")
async def set_trust_level(
    body: TrustLevelBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    if body.trust_level not in VALID_LEVELS:
        raise HTTPException(400, "trust_level must be L1, L2, or L3")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    await db.dev_users.update_one(
        {"user_id": me["user_id"]},
        {"$set": {"trust_level": body.trust_level}},
    )
    return {"ok": True, "trust_level": body.trust_level}


async def get_user_trust_level(db, user_id: str) -> str:
    """Helper used by loop_engine + finding_fix_applier to read the
    caller's preference. Defaults to L2."""
    if db is None or not user_id:
        return DEFAULT_LEVEL
    try:
        row = await db.dev_users.find_one(
            {"user_id": user_id}, {"_id": 0, "trust_level": 1},
        )
        level = (row or {}).get("trust_level") or DEFAULT_LEVEL
        return level if level in VALID_LEVELS else DEFAULT_LEVEL
    except Exception:
        return DEFAULT_LEVEL
