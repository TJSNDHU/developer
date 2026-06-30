"""
routers/hosted_deploy.py — Vercel / Netlify deploy-hook bridge.

Distinct from `routers/deploy.py` (SSH-based custom-server deploy). This
endpoint set is for **hosted** deploy providers that expose webhook URLs:

  * Vercel deploy hooks  (Settings → Git → Deploy Hooks)
  * Netlify build hooks  (Site → Build & deploy → Build hooks)

The user creates the hook once on the provider side and pastes the URL
into AUREM. We store it encrypted (same HKDF-Fernet vault used for
GitHub PATs) so a DB dump never leaks deploy access. From that point on
the "Ship to Live" button in the project view fires a `POST {hook_url}`
which kicks a fresh build of the latest commit.

Endpoints (mounted under /api/aurem-dev/hosted-deploy):
  POST  /connect                 {project_id, provider, hook_url}
  GET   /status/{project_id}
  POST  /ship                    {project_id}
  DELETE /disconnect/{project_id}
"""
from __future__ import annotations

import logging
import re
import time
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db
from cto_services.crypto import encrypt, decrypt

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hosted-deploy", tags=["Hosted Deploy"])

_VERCEL_HOOK_RX = re.compile(
    r"^https://api\.vercel\.com/v1/integrations/deploy/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/?$"
)
_NETLIFY_HOOK_RX = re.compile(
    r"^https://api\.netlify\.com/build_hooks/[A-Za-z0-9]+/?$"
)


class ConnectBody(BaseModel):
    project_id: str
    provider:   Literal["vercel", "netlify"]
    hook_url:   str = Field(..., min_length=20, max_length=400)


class ShipBody(BaseModel):
    project_id: str


def _validate_hook(provider: str, url: str) -> None:
    """Strict pattern match at connect-time. Catches typos and the
    wrong-provider mistake (a Netlify URL pasted into the Vercel field)
    before the user ships and gets a confusing 404 from the wrong host."""
    rx = _VERCEL_HOOK_RX if provider == "vercel" else _NETLIFY_HOOK_RX
    if not rx.match((url or "").strip()):
        raise HTTPException(
            400,
            f"That doesn't look like a valid {provider} deploy hook URL. "
            "Create one on the provider (Vercel → Project → Settings → "
            "Git → Deploy Hooks, or Netlify → Site → Build & deploy → "
            "Build hooks) and paste the full URL here.",
        )


@router.post("/connect")
async def connect(body: ConnectBody,
                  authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    _validate_hook(body.provider, body.hook_url)
    proj = await db.cto_projects.find_one(
        {"project_id": body.project_id, "user_id": user["user_id"]},
        {"_id": 0, "project_id": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    enc = encrypt(body.hook_url.strip())
    await db.cto_projects.update_one(
        {"project_id": body.project_id, "user_id": user["user_id"]},
        {"$set": {
            "deploy_provider":     body.provider,
            "deploy_hook_enc":     enc,
            "deploy_connected_at": time.time(),
        }},
    )
    return {"ok": True, "provider": body.provider}


@router.get("/status/{project_id}")
async def status(project_id: str,
                 authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user["user_id"]},
        {"_id": 0, "deploy_provider": 1, "deploy_connected_at": 1,
         "last_deploy_at": 1, "last_deploy_status": 1,
         "last_deploy_response": 1},
    )
    if not proj:
        # Iter 212m-154 — graceful empty-state.  Previously raised
        # HTTP 404 here, which leaked a "Failed to load resource"
        # error into the browser console on every /deploy visit for
        # projects without an active deploy connection (caught in
        # iter 212m-153 prod QA).  The UI already handles the
        # "not connected" case, so we return a 200 with the same
        # shape as the connected case but every flag set to False.
        return {
            "ok":            True,
            "connected":     False,
            "project_found": False,
            "provider":      None,
            "connected_at":  None,
            "last_deploy":   None,
            "last_status":   None,
            "last_response": "",
        }
    return {
        "ok":            True,
        "connected":     bool(proj.get("deploy_provider")),
        "project_found": True,
        "provider":      proj.get("deploy_provider"),
        "connected_at":  proj.get("deploy_connected_at"),
        "last_deploy":   proj.get("last_deploy_at"),
        "last_status":   proj.get("last_deploy_status"),
        "last_response": (proj.get("last_deploy_response") or "")[:200],
    }


@router.post("/ship")
async def ship(body: ShipBody,
               authorization: Optional[str] = Header(None)) -> dict:
    """Fire the stored deploy hook. The provider kicks a fresh build
    from the latest commit on the connected branch."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    proj = await db.cto_projects.find_one(
        {"project_id": body.project_id, "user_id": user["user_id"]},
        {"_id": 0, "deploy_provider": 1, "deploy_hook_enc": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    enc = proj.get("deploy_hook_enc")
    if not enc:
        raise HTTPException(
            409,
            "No deploy hook connected. Click 'Connect deploy' in the "
            "project view and paste your Vercel or Netlify deploy hook URL.",
        )
    try:
        hook_url = decrypt(enc)
    except Exception:
        logger.exception("hosted-deploy/ship: hook decrypt failed")
        raise HTTPException(500, "Deploy hook is corrupted — reconnect it.")

    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(hook_url)
            ok = 200 <= r.status_code < 300
            snippet = (r.text or "")[:200]
    except Exception as e:
        logger.exception("hosted-deploy/ship: hook POST crashed")
        await db.cto_projects.update_one(
            {"project_id": body.project_id, "user_id": user["user_id"]},
            {"$set": {"last_deploy_at": time.time(),
                      "last_deploy_status": "error",
                      "last_deploy_response": str(e)[:300]}},
        )
        raise HTTPException(502, f"Deploy provider unreachable: {e}")

    await db.cto_projects.update_one(
        {"project_id": body.project_id, "user_id": user["user_id"]},
        {"$set": {"last_deploy_at": time.time(),
                  "last_deploy_status": "queued" if ok else "error",
                  "last_deploy_response": snippet}},
    )
    if not ok:
        raise HTTPException(
            502,
            f"Provider rejected the hook (HTTP {r.status_code}). Most "
            "likely the hook was deleted on the provider — reconnect "
            f"with a fresh URL. Body: {snippet}",
        )
    return {
        "ok":               True,
        "provider":         proj.get("deploy_provider"),
        "status":           "queued",
        "provider_response": snippet,
    }


@router.delete("/disconnect/{project_id}")
async def disconnect(project_id: str,
                     authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    r = await db.cto_projects.update_one(
        {"project_id": project_id, "user_id": user["user_id"]},
        {"$unset": {"deploy_provider":     "",
                    "deploy_hook_enc":     "",
                    "deploy_connected_at": ""}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Project not found")
    return {"ok": True}
