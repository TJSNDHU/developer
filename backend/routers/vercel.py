"""
routers/vercel.py — Iter 212m-84

Thin HTTP surface for the Vercel integration card in Settings UI.
Real tool execution happens inside ORA's chat tool-use loop (see
`services/vercel_skills.py`); these routes are only for:

  GET  /integrations/vercel/status     — connected? whose account? plan?
  GET  /integrations/vercel/tools      — list of MCP-style tool specs
                                        (so the Settings card can show
                                        "8 tools available to ORA")
  GET  /integrations/vercel/audit      — last 25 tool calls for this
                                        user (or all, for admin)
  POST /integrations/vercel/execute    — direct manual tool invocation
                                        (for the "try it" button in
                                        the Settings card, before
                                        going to chat)

Auth: standard `Authorization: Bearer <JWT>` (decodes via
existing `routers.auth.get_current_user`). For the shared-token
mode every authenticated user can invoke read-only tools; write
tools require the user to be `is_admin` (founder).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel

from cto_services.db import get_db
from cto_services.auth import current_dev
from services.vercel_skills import (
    VERCEL_TOOLS, VERCEL_TOOL_SPECS, vercel_account_info,
)

router = APIRouter(prefix="/integrations/vercel", tags=["vercel"])


# Reuse the project's existing auth helper.
async def _resolve_user(authorization: Optional[str]) -> dict:
    payload = await current_dev(authorization)
    if not payload or not payload.get("user_id"):
        raise HTTPException(401, "Invalid token")
    db = get_db()
    user = await db.dev_users.find_one({"user_id": payload["user_id"]})
    if not user:
        raise HTTPException(401, "User not found")
    return user


# ── 1) STATUS ────────────────────────────────────────────────────────

@router.get("/status")
async def vercel_status(authorization: Optional[str] = Header(None)):
    user = await _resolve_user(authorization)
    token = (os.environ.get("VERCEL_API_TOKEN") or "").strip()
    if not token:
        return {
            "connected":      False,
            "mode":            "shared-token",
            "reason":          "VERCEL_API_TOKEN not set",
            "tool_count":      len(VERCEL_TOOLS),
        }
    # Probe the actual account so we can show "Connected as
    # ora@auremcto.com (hobby)" in the UI.
    info = await vercel_account_info({"user_id": user["user_id"]}, {})
    if not info.get("ok"):
        return {
            "connected":  False,
            "mode":       "shared-token",
            "reason":     info.get("error") or "Vercel auth failed",
            "tool_count": len(VERCEL_TOOLS),
        }
    return {
        "connected":  True,
        "mode":       "shared-token",
        "account":    info["account"],
        "tool_count": len(VERCEL_TOOLS),
        "future_mcp_oauth": True,  # we will swap to mcp.vercel.com OAuth later
    }


# ── 2) TOOL CATALOGUE ────────────────────────────────────────────────

@router.get("/tools")
async def list_tools(authorization: Optional[str] = Header(None)):
    await _resolve_user(authorization)  # auth-gated
    return {"count": len(VERCEL_TOOL_SPECS), "tools": VERCEL_TOOL_SPECS}


# ── 3) AUDIT LOG ─────────────────────────────────────────────────────

@router.get("/audit")
async def audit_log(authorization: Optional[str] = Header(None),
                    limit: int = 25):
    user = await _resolve_user(authorization)
    db = get_db()
    limit = max(1, min(int(limit or 25), 100))
    # admins see all; others see their own
    flt: dict = {} if user.get("is_admin") else {"user_id": user["user_id"]}
    cursor = db.vercel_tool_audit.find(flt, {"_id": 0}).sort(
        "created_at", -1).limit(limit)
    entries = await cursor.to_list(length=limit)
    return {"count": len(entries), "entries": entries}


# ── 4) DIRECT EXECUTE (for Settings "Try it" button) ─────────────────

class _ExecuteIn(BaseModel):
    tool: str
    args: dict = {}


_WRITE_TOOLS = {
    "vercel_trigger_deploy_hook",
    "vercel_create_project",
    "vercel_pause_project",
    "vercel_resume_project",
    "vercel_add_domain",
    "vercel_delete_project",
}


@router.post("/execute")
async def execute_tool(payload: _ExecuteIn,
                       authorization: Optional[str] = Header(None)):
    user = await _resolve_user(authorization)
    fn = VERCEL_TOOLS.get(payload.tool)
    if not fn:
        raise HTTPException(404, f"Unknown vercel tool '{payload.tool}'")
    if payload.tool in _WRITE_TOOLS and not user.get("is_admin"):
        raise HTTPException(403,
            "Write tools require admin (founder) role for safety")
    ctx = {"user_id": user["user_id"]}
    out = await fn(ctx, payload.args or {})
    return {
        "tool":  payload.tool,
        "ok":    out.get("ok", False),
        "data":  out,
        "ts":    datetime.now(timezone.utc).isoformat(),
    }
