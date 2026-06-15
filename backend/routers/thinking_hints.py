"""
routers/thinking_hints.py — Iter 158

Two surfaces:
  GET  /api/aurem-dev/thinking-hint            (auth required, returns 0-1)
  GET  /api/aurem-dev/admin/thinking-hints     (admin list-all)
  POST /api/aurem-dev/admin/thinking-hints     (admin create)
  PUT  /api/aurem-dev/admin/thinking-hints/{id} (admin update)
  DELETE …                                     (admin delete)

The user-facing GET is intentionally cheap — one Mongo read every 60s
shared across all chat sessions thanks to the in-process cache. Admin
mutations bust that cache via `bust_cache()`.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db
from routers.admin import _require_admin
from services.thinking_hints import bust_cache, pick_hint

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ThinkingHints"])


# ── Models ────────────────────────────────────────────────────────────

class HintIn(BaseModel):
    """Create / update payload from the admin UI."""
    hint_id:   Optional[str] = Field(None, max_length=80)
    tier:      str = Field(..., pattern=r"^(free|starter|pro|team|founder)$")
    emoji:     Optional[str] = Field("", max_length=4)
    headline:  str = Field(..., min_length=1, max_length=80)
    body:      str = Field(..., min_length=1, max_length=180)
    cta_text:  Optional[str] = Field("", max_length=32)
    cta_link:  Optional[str] = Field("", max_length=200)
    active:    bool = True
    weight:    int = Field(10, ge=1, le=100)


# ── Public — used by the chat UI's "thinking…" pill ──────────────────

@router.get("/thinking-hint")
async def get_thinking_hint(authorization: Optional[str] = Header(None)):
    """Return a single hint matched to the caller's tier, or null.

    Also returns the global `enabled` + `delay_ms` config so the
    frontend can honour an admin kill-switch without a redeploy."""
    user = await current_dev(authorization)
    tier = (user.get("tier") or "free").lower()
    db = get_db()

    # Global config — single doc at `_id="config"` in thinking_hints_config.
    # Missing doc → defaults (enabled=True, delay_ms=600).
    enabled = True
    delay_ms = 600
    if db is not None:
        cfg = await db.thinking_hints_config.find_one({"_id": "config"})
        if cfg:
            if "enabled" in cfg:
                enabled = bool(cfg["enabled"])
            if "delay_ms" in cfg:
                try:
                    delay_ms = max(200, min(5000, int(cfg["delay_ms"])))
                except (TypeError, ValueError):
                    delay_ms = 600

    if not enabled:
        return {"ok": True, "hint": None, "tier": tier,
                "enabled": False, "delay_ms": delay_ms}

    hint = await pick_hint(db, tier)
    return {"ok": True, "hint": hint, "tier": tier,
            "enabled": True, "delay_ms": delay_ms}


# ── Admin CRUD ───────────────────────────────────────────────────────

@router.get("/admin/thinking-hints")
async def admin_list_hints(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    cursor = db.thinking_hints.find({}).sort([("tier", 1), ("weight", -1)])
    rows = []
    async for r in cursor:
        r["_id"] = str(r["_id"])
        rows.append(r)
    return {"ok": True, "items": rows, "count": len(rows)}


@router.post("/admin/thinking-hints")
async def admin_create_hint(payload: HintIn,
                            authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    now = time.time()
    doc = payload.dict()
    if not doc.get("hint_id"):
        doc["hint_id"] = f"hint_{uuid.uuid4().hex[:10]}"
    # Idempotent insert: explicit hint_id must be unique.
    if await db.thinking_hints.find_one({"hint_id": doc["hint_id"]}):
        raise HTTPException(409, f"hint_id '{doc['hint_id']}' already exists")
    doc["created_at"] = now
    doc["updated_at"] = now
    await db.thinking_hints.insert_one(doc)
    bust_cache()
    return {"ok": True, "hint_id": doc["hint_id"]}


@router.put("/admin/thinking-hints/{hint_id}")
async def admin_update_hint(hint_id: str, payload: HintIn,
                            authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    update = payload.dict(exclude_unset=False)
    update.pop("hint_id", None)                 # path param wins
    update["updated_at"] = time.time()
    res = await db.thinking_hints.update_one(
        {"hint_id": hint_id}, {"$set": update},
    )
    if res.matched_count == 0:
        raise HTTPException(404, f"hint '{hint_id}' not found")
    bust_cache()
    return {"ok": True, "modified": res.modified_count}


@router.delete("/admin/thinking-hints/{hint_id}")
async def admin_delete_hint(hint_id: str,
                            authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    res = await db.thinking_hints.delete_one({"hint_id": hint_id})
    if res.deleted_count == 0:
        raise HTTPException(404, f"hint '{hint_id}' not found")
    bust_cache()
    return {"ok": True, "deleted": res.deleted_count}


# ── Global config (enabled + delay) ──────────────────────────────────

class HintsConfigIn(BaseModel):
    enabled:  bool = True
    delay_ms: int  = Field(600, ge=200, le=5000)


@router.get("/admin/thinking-hints-config")
async def admin_get_config(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    cfg = await db.thinking_hints_config.find_one({"_id": "config"}) or {}
    cfg.pop("_id", None)
    return {
        "ok": True,
        "enabled":  bool(cfg.get("enabled", True)),
        "delay_ms": int(cfg.get("delay_ms", 600)),
    }


@router.post("/admin/thinking-hints-config")
async def admin_set_config(payload: HintsConfigIn,
                           authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    await db.thinking_hints_config.update_one(
        {"_id": "config"},
        {"$set": {
            "enabled":    payload.enabled,
            "delay_ms":   payload.delay_ms,
            "updated_at": time.time(),
        }},
        upsert=True,
    )
    bust_cache()
    return {"ok": True}
