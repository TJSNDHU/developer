"""
routers/github_oauth.py — AUREM Dev
OAuth + repo-list endpoints. Mounted under /api/aurem-dev/github/oauth/*
so it does not collide with the legacy /github/{status,push} surface.
"""
from __future__ import annotations
import logging
import os
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import RedirectResponse

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.github_oauth import auth_url, exchange, gh_user, gh_repos

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github/oauth", tags=["GitHub OAuth"])


def _frontend_settings_url(query: str) -> str:
    # APP_URL must be set in deployment env (e.g. https://auremcto.com).
    # No hardcoded fallback so misconfiguration fails loud, not silent.
    base = (os.getenv("APP_URL") or "").rstrip("/")
    if not base:
        raise HTTPException(500, "APP_URL not configured on this deployment")
    return f"{base}/settings?{query}"


@router.get("/connect")
async def connect(
    authorization: Optional[str] = Header(None),
    auth: Optional[str] = Query(None),
):
    """Kick off OAuth — redirects browser to GitHub consent screen.
    JWT may arrive via the standard Authorization header (API call) or as
    a `?auth=` query param (browser navigation from the Settings page)."""
    if not authorization and auth:
        authorization = f"Bearer {auth}"
    user = await current_dev(authorization)
    state = f"{user['user_id']}:{uuid.uuid4().hex}"
    db = get_db()
    if db is not None:
        await db.oauth_states.insert_one({
            "state": state,
            "user_id": user["user_id"],
            "ts": time.time(),
        })
    return RedirectResponse(url=auth_url(state))


@router.get("/callback")
async def callback(code: str = Query(...), state: str = Query(...)):
    """GitHub redirects here after user authorises. We exchange + store."""
    if ":" not in state:
        raise HTTPException(400, "Invalid state")
    user_id = state.split(":", 1)[0]
    if not user_id:
        raise HTTPException(400, "Invalid state")

    db = get_db()
    # Verify state was actually issued by us
    if db is not None:
        s = await db.oauth_states.find_one({"state": state, "user_id": user_id})
        if not s:
            raise HTTPException(400, "Unknown OAuth state")

    try:
        token = await exchange(code)
        info = await gh_user(token)
    except Exception as e:
        logger.error(f"[oauth] callback failed: {e!r}")
        return RedirectResponse(
            url=_frontend_settings_url(f"github=error&msg={e}")
        )

    if db is not None:
        await db.dev_users.update_one(
            {"user_id": user_id},
            {"$set": {"github": {
                "access_token": token,
                "login": info.get("login"),
                "avatar_url": info.get("avatar_url", ""),
                "connected_at": time.time(),
            }}},
        )
        await db.oauth_states.delete_one({"state": state})

    return RedirectResponse(
        url=_frontend_settings_url(
            f"github=connected&login={info.get('login','')}"
        )
    )


@router.get("/status")
async def status(authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True, "connected": False}
    u = await db.dev_users.find_one(
        {"user_id": user["user_id"]}, {"_id": 0, "github": 1}
    )
    gh = (u or {}).get("github") or {}
    if not gh.get("access_token"):
        return {"ok": True, "connected": False}
    return {
        "ok": True,
        "connected": True,
        "login": gh.get("login"),
        "avatar_url": gh.get("avatar_url"),
        "connected_at": gh.get("connected_at"),
    }


@router.get("/repos")
async def repos(authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    u = await db.dev_users.find_one(
        {"user_id": user["user_id"]}, {"_id": 0, "github": 1}
    )
    token = ((u or {}).get("github") or {}).get("access_token")
    if not token:
        raise HTTPException(400, "GitHub not connected")
    data = await gh_repos(token)
    return {
        "ok": True,
        "repos": [
            {
                "name": r.get("name"),
                "full_name": r.get("full_name"),
                "private": r.get("private", False),
                "url": r.get("html_url"),
                "default_branch": r.get("default_branch", "main"),
                "updated_at": r.get("updated_at"),
            }
            for r in data
        ],
    }


@router.delete("/disconnect")
async def disconnect(authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is not None:
        await db.dev_users.update_one(
            {"user_id": user["user_id"]},
            {"$unset": {"github": ""}},
        )
    return {"ok": True}
