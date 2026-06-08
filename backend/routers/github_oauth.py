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

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import RedirectResponse

from cto_services.auth import create_token, current_dev
from cto_services.db import get_db
from services.github_oauth import auth_url, exchange, gh_user, gh_repos
from services.usage import is_founder_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github/oauth", tags=["GitHub OAuth"])


def _frontend_url(path: str, query: str) -> str:
    # APP_URL must be set in deployment env (e.g. https://auremcto.com).
    # No hardcoded fallback so misconfiguration fails loud, not silent.
    base = (os.getenv("APP_URL") or "").rstrip("/")
    if not base:
        raise HTTPException(500, "APP_URL not configured on this deployment")
    return f"{base}{path}?{query}"


def _frontend_settings_url(query: str) -> str:
    return _frontend_url("/settings", query)


async def _gh_primary_email(token: str) -> Optional[str]:
    """GitHub's /user endpoint returns email=null when the user marked
    it private. Hit /user/emails (needs read:user / user:email scope
    which our SCOPES list already includes via read:user) to find the
    verified primary."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.github.com/user/emails",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            r.raise_for_status()
            for row in r.json() or []:
                if row.get("primary") and row.get("verified"):
                    return row.get("email")
            for row in r.json() or []:
                if row.get("verified"):
                    return row.get("email")
    except Exception as e:
        logger.warning(f"[oauth] /user/emails fetch failed: {e!r}")
    return None


@router.get("/connect")
async def connect(
    authorization: Optional[str] = Header(None),
    auth: Optional[str] = Query(None),
    signup: Optional[str] = Query(None),
):
    """Kick off OAuth.

    Two modes:
      • **Connect** (default) — used by an already-logged-in user from
        Settings to attach GitHub to their existing AUREM account. JWT
        required (Authorization header or `?auth=` query for browser
        navigation).
      • **Signup / Sign-in** (`?signup=1`) — true OAuth-first auth. No
        JWT required. The callback either logs the matching user in or
        creates a new account from the GitHub identity.
    """
    db = get_db()
    if signup in ("1", "true", "yes"):
        # Anonymous OAuth start — state nonce only, no user binding.
        nonce = uuid.uuid4().hex
        state = f"signup:{nonce}"
        if db is not None:
            await db.oauth_states.insert_one({
                "state":   state,
                "mode":    "signup",
                "user_id": None,
                "ts":      time.time(),
            })
        return RedirectResponse(url=auth_url(state))

    # Connect mode — must be authenticated.
    if not authorization and auth:
        authorization = f"Bearer {auth}"
    user = await current_dev(authorization)
    state = f"{user['user_id']}:{uuid.uuid4().hex}"
    if db is not None:
        await db.oauth_states.insert_one({
            "state":   state,
            "mode":    "connect",
            "user_id": user["user_id"],
            "ts":      time.time(),
        })
    return RedirectResponse(url=auth_url(state))


@router.get("/callback")
async def callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
):
    """GitHub redirects here after the user finishes the consent flow.

    Two non-success paths to handle gracefully (without 422):
      1. User clicked **Cancel** on GitHub → `?error=access_denied&...`
         (no `code` present). We redirect to the frontend with a friendly
         "github=cancelled" message instead of FastAPI's stock 422.
      2. Any other GitHub-side error → same friendly redirect.
    Success path: `?code=...&state=...` exchanges the code and continues.
    """
    # Iter 109 — user-cancelled the GitHub consent screen.
    if error or not code:
        logger.info("[oauth] callback non-success: error=%s code_present=%s state=%s",
                    error, bool(code), state)
        # Decide where to send them: signup flow → /signup, connect flow → /settings
        target_path = "/settings"
        if state and state.startswith("signup:"):
            target_path = "/signup"
        # Clean up the dangling state row so it can't be replayed.
        try:
            db = get_db()
            if db is not None and state:
                await db.oauth_states.delete_one({"state": state})
        except Exception:
            pass
        reason = error or "missing_code"
        # Keep the redirect URL short — frontend reads ?github=cancelled
        return RedirectResponse(
            url=_frontend_url(target_path, f"github=cancelled&reason={reason}")
        )

    if not state or ":" not in state:
        raise HTTPException(400, "Invalid state")
    mode_or_user, _, _ = state.partition(":")

    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    # Verify state was actually issued by us
    s = await db.oauth_states.find_one({"state": state})
    if not s:
        raise HTTPException(400, "Unknown OAuth state")
    flow = s.get("mode") or ("signup" if mode_or_user == "signup" else "connect")

    # Exchange code → token → GitHub user (common to both flows).
    try:
        token = await exchange(code)
        info  = await gh_user(token)
    except Exception as e:
        logger.error(f"[oauth] callback failed: {e!r}")
        await db.oauth_states.delete_one({"state": state})
        return RedirectResponse(
            url=_frontend_settings_url(f"github=error&msg={e}")
        )

    await db.oauth_states.delete_one({"state": state})

    gh_login  = (info or {}).get("login") or ""
    gh_avatar = (info or {}).get("avatar_url") or ""
    gh_email  = (info or {}).get("email")

    # ── Signup / sign-in flow ─────────────────────────────────────────
    if flow == "signup":
        # Pull a verified email if GitHub hid it on the public profile.
        if not gh_email:
            gh_email = await _gh_primary_email(token)
        # Find an existing AUREM account to log in.
        existing = None
        if gh_login:
            existing = await db.dev_users.find_one(
                {"github.login": gh_login}, {"_id": 0},
            )
        if not existing and gh_email:
            existing = await db.dev_users.find_one(
                {"email": gh_email}, {"_id": 0},
            )

        if existing:
            user_id   = existing["user_id"]
            user_mail = existing.get("email") or gh_email or ""
            is_admin  = bool(existing.get("is_admin"))
            await db.dev_users.update_one(
                {"user_id": user_id},
                {"$set": {"github": {
                    "access_token": token,
                    "login":        gh_login,
                    "avatar_url":   gh_avatar,
                    "connected_at": time.time(),
                }}},
            )
        else:
            # Brand-new account, no password (GitHub-only sign-in).
            user_id   = uuid.uuid4().hex
            # If GitHub still hid the email, fall back to a stable
            # synthetic so the unique constraint never bites us.
            user_mail = gh_email or f"{gh_login or user_id}@users.noreply.github.com"
            is_founder = is_founder_email(user_mail)
            is_admin   = is_founder
            tier       = "founder" if is_founder else "free"
            tokens     = 10**9 if is_founder else 1000
            await db.dev_users.insert_one({
                "user_id":          user_id,
                "email":            user_mail,
                "name":             gh_login or user_mail.split("@")[0],
                "password":         None,            # OAuth-only user
                "auth_provider":    "github",
                "tier":             tier,
                "tokens_remaining": tokens,
                "is_admin":         is_admin,
                "is_unlimited":     is_admin,
                "github": {
                    "access_token": token,
                    "login":        gh_login,
                    "avatar_url":   gh_avatar,
                    "connected_at": time.time(),
                },
            })

        jwt_token = create_token(user_id, user_mail, is_admin=is_admin)
        # Bounce the browser back to a tiny finish page that stashes
        # the token in localStorage and routes to /dashboard. Token
        # goes in the URL fragment (`#`) so it never hits server logs
        # or Referer headers.
        base = (os.getenv("APP_URL") or "").rstrip("/")
        if not base:
            raise HTTPException(500, "APP_URL not configured")
        return RedirectResponse(
            url=(
                f"{base}/oauth-finish"
                f"#token={jwt_token}"
                f"&login={gh_login}"
            )
        )

    # ── Connect-existing-account flow (original behaviour) ────────────
    user_id = s.get("user_id") or mode_or_user
    if not user_id:
        raise HTTPException(400, "Invalid state")
    await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {"github": {
            "access_token": token,
            "login":        gh_login,
            "avatar_url":   gh_avatar,
            "connected_at": time.time(),
        }}},
    )
    return RedirectResponse(
        url=_frontend_settings_url(f"github=connected&login={gh_login}")
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
