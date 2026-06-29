"""
routers/repo_status.py — Iter 212m-125

Real-time GitHub connection-status pings for every repo in the
user's sidebar.  Per founder spec: each sidebar entry must carry a
live coloured dot:
  • green   — GitHub `GET /repos/{owner}/{repo}` returns 200 with
              the user's PAT (or OAuth fallback)
  • red     — token missing, revoked, or repo not reachable
              (401/403/404, DNS, timeout)
  • yellow  — driven by the FRONTEND while a check is in flight;
              the backend never returns yellow itself.

Why a dedicated endpoint instead of folding into /projects/list:
  • /projects/list is hot — every page mount hits it and we don't
    want to fan out N GitHub API calls on every load.
  • Status is best-fetched lazily after the sidebar paints, so the
    UI is instant and the dots fill in within ~1 s.
  • Polling cadence (30 s) is owned by the client; the backend just
    answers "what's the truth right now".

Endpoint:
  GET /api/aurem-dev/cto/projects/connection-status
       Returns: {ok, statuses: [
         {project_id, owner, repo, branch, status, http_code,
          checked_at, auth: "pat"|"oauth"|"none", error?}
       ]}
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException

from cto_services.auth import current_dev
from cto_services.db import get_db
from cto_services.crypto import decrypt

logger = logging.getLogger("aurem-dev.repo_status")
router = APIRouter(prefix="/cto/projects", tags=["Repo Status"])


_TIMEOUT_S      = 5.0
_MAX_PARALLEL   = 8       # don't pound GitHub from a single user
_CACHE_TTL_S    = 8       # short cache to swallow duplicate polls
_CACHE: dict[str, dict] = {}        # keyed by project_id


async def _decrypt_pat(user_id: str, ciphertext: Optional[str]) -> Optional[str]:
    if not ciphertext:
        return None
    try:
        return await decrypt(user_id, ciphertext, kind="github_token")
    except Exception:                                     # noqa: BLE001
        return None


async def _check_one(client: httpx.AsyncClient, *, project_id: str,
                      owner: str, repo: str, token: str,
                      auth: str) -> dict:
    """Hit `GET /repos/{owner}/{repo}` once with a short timeout."""
    now = time.time()
    try:
        r = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={
                "Authorization": f"token {token}",
                "Accept":        "application/vnd.github+json",
                "User-Agent":    "aurem-repo-status",
            },
        )
        # Anything 2xx = repo reachable + token authorised.
        if 200 <= r.status_code < 300:
            return {"project_id": project_id, "status": "connected",
                    "http_code": r.status_code, "checked_at": now,
                    "auth": auth, "owner": owner, "repo": repo}
        # 401/403 = bad/expired token; 404 = no access or deleted.
        return {"project_id": project_id, "status": "disconnected",
                "http_code": r.status_code, "checked_at": now,
                "auth": auth, "owner": owner, "repo": repo,
                "error": "github_rejected" if r.status_code in (401, 403)
                         else ("repo_not_found" if r.status_code == 404
                               else f"http_{r.status_code}")}
    except (httpx.TimeoutException, httpx.NetworkError) as e:
        return {"project_id": project_id, "status": "disconnected",
                "http_code": 0, "checked_at": now,
                "auth": auth, "owner": owner, "repo": repo,
                "error": f"network: {type(e).__name__}"}
    except Exception as e:                                # noqa: BLE001
        logger.warning("repo-status raised for %s/%s: %r", owner, repo, e)
        return {"project_id": project_id, "status": "disconnected",
                "http_code": 0, "checked_at": now,
                "auth": auth, "owner": owner, "repo": repo,
                "error": "unexpected"}


@router.get("/connection-status")
async def connection_status(authorization: str = Header(None)) -> dict:
    """Batched live check of every project's GitHub connectivity.

    The endpoint:
      1. Loads the caller's project rows.
      2. For each row, picks the freshest auth token:
         PAT → user's OAuth token → no auth at all.
      3. Fan-outs `GET /repos/{owner}/{repo}` calls in parallel
         (capped at _MAX_PARALLEL) with a 5 s timeout each.
      4. Returns a flat array the sidebar maps to dots.

    A tiny TTL cache (_CACHE_TTL_S = 8 s) coalesces duplicate polls
    while the user is bouncing between routes.
    """
    user = await current_dev(authorization)
    user_id = user["user_id"]
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    projs = await db.cto_projects.find(
        {"user_id": user_id},
        {"_id": 0, "project_id": 1, "name": 1,
         "github_owner": 1, "github_repo": 1, "branch": 1,
         "github_token": 1},
    ).sort("created_at", -1).to_list(50)

    # Pull the user's OAuth token once — many projects may share it.
    me = await db.dev_users.find_one(
        {"user_id": user_id}, {"_id": 0, "github": 1},
    )
    oauth_token = ((me or {}).get("github") or {}).get("access_token") or None

    now = time.time()
    tasks: list = []
    no_check: list[dict] = []          # rows with no creds at all
    for p in projs:
        pid    = p.get("project_id")
        owner  = (p.get("github_owner") or "").strip()
        repo   = (p.get("github_repo")  or "").strip()
        # Coalesce duplicate polls per project.
        cached = _CACHE.get(pid)
        if cached and now - cached["checked_at"] < _CACHE_TTL_S:
            tasks.append(cached)            # dict — runner short-circuits
            continue
        if not (owner and repo):
            no_check.append({"project_id": pid, "status": "disconnected",
                             "http_code": 0, "checked_at": now,
                             "auth": "none", "owner": owner, "repo": repo,
                             "error": "repo_not_set"})
            continue
        pat = await _decrypt_pat(user_id, p.get("github_token"))
        if pat:
            token, auth = pat, "pat"
        elif oauth_token:
            token, auth = oauth_token, "oauth"
        else:
            no_check.append({"project_id": pid, "status": "disconnected",
                             "http_code": 0, "checked_at": now,
                             "auth": "none", "owner": owner, "repo": repo,
                             "error": "no_token"})
            continue
        tasks.append(("check", pid, owner, repo, token, auth))

    # Run the live HTTP checks in parallel with a semaphore.
    sem = asyncio.Semaphore(_MAX_PARALLEL)
    results: list[dict] = list(no_check)

    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        async def _runner(item):
            # Cached sleep-result entry comes back as dict directly.
            if isinstance(item, dict):
                results.append(item)
                return
            _, pid, owner, repo, token, auth = item
            async with sem:
                r = await _check_one(
                    client, project_id=pid, owner=owner, repo=repo,
                    token=token, auth=auth,
                )
            _CACHE[pid] = r
            results.append(r)

        await asyncio.gather(*[_runner(t) for t in tasks])

    # Stable order — match the projects list ordering so the sidebar
    # rows don't shuffle every refresh.
    by_pid = {r["project_id"]: r for r in results}
    ordered = [by_pid[p["project_id"]] for p in projs if p["project_id"] in by_pid]

    # Iter 212m-126 — Auto-heal hook.  Any project that came back
    # `disconnected` immediately triggers a fire-and-forget heal
    # task.  The next poll (≈ 30 s later) will pick up the green
    # dot if the heal succeeded — entirely backend-driven, no UI
    # action required from the user.
    try:
        from services.repo_heal import schedule_heal
        for s in ordered:
            if s.get("status") == "disconnected":
                schedule_heal(
                    db=db, user_id=user_id,
                    project_id=s["project_id"], prior_status=s,
                )
    except Exception as e:                                # noqa: BLE001
        logger.warning("auto-heal scheduling soft-failed: %r", e)

    return {"ok": True, "statuses": ordered, "checked_at": now}
