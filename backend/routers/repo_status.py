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
# arch: allow-http — Per-sidebar-repo GitHub liveness pings (iter 212m-225)
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
    """Probe GitHub with the SAME permission surface the ORA tools use.

    Iter 212m-192 — Previously this hit `GET /repos/{owner}/{repo}`
    which is a metadata endpoint that returns 200 for any token with
    basic repo visibility. Ask Advisor tools (`read_repo_file`,
    `list_repo_files`, `search_repo`) actually call
    `GET /repos/{owner}/{repo}/contents/{path}`, which requires the
    `Contents: Read` scope for private repos (or `public_repo` scope
    for public). The mismatch let a green "connected" dot lie: users
    saw green in the sidebar while every actual tool call returned
    401 in the chat.

    New behaviour: probe the **contents** endpoint (root listing)
    instead. Any success is a hard guarantee the tools will succeed;
    401/403 now correctly surface as `disconnected · github_rejected`,
    which the UI already renders as a red dot with the "click to
    re-link" tooltip.
    """
    now = time.time()
    try:
        r = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/contents/",
            headers={
                "Authorization": f"token {token}",
                "Accept":        "application/vnd.github+json",
                "User-Agent":    "aurem-repo-status",
            },
        )
        # Anything 2xx = repo reachable AND contents-scope granted —
        # same permission surface the ORA tools rely on.
        if 200 <= r.status_code < 300:
            return {"project_id": project_id, "status": "connected",
                    "http_code": r.status_code, "checked_at": now,
                    "auth": auth, "owner": owner, "repo": repo}
        # 401/403 = bad/expired token or missing Contents:Read scope.
        # 404 = repo doesn't exist for this token (private + no access
        # or deleted). Every non-2xx maps to disconnected + a reason.
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


# ── Iter 212m-136 — Repo cleanup pipeline ────────────────────────────
#
# When a project's GitHub repo is deleted/renamed, /connection-status
# correctly flags it red with `error: "repo_not_found"`. The user has
# always had a per-row Settings deep-link (Iter 212m-133) but no
# bulk path. /cleanup-summary + /cleanup-delete close that gap:
#
#   GET  /cto/projects/cleanup-summary
#        → {count, broken: [{project_id, name, owner, repo, error}]}
#   POST /cto/projects/cleanup-delete  body: {project_ids: [...]}
#        → {deleted: int, audit_id: str}
#
# Each delete writes an audit row to `repo_cleanup_audit` so a future
# undo / report path has the data. Both endpoints reuse the same
# connection-status logic so the "broken" set is always fresh.

_BROKEN_REASONS = {"repo_not_found", "github_rejected", "repo_not_set", "no_token"}


@router.get("/cleanup-summary")
async def cleanup_summary(authorization: str = Header(None)) -> dict:
    """Return projects with persistent connection failures so the
    dashboard banner can offer a one-click bulk-cleanup path.

    Uses the same connection-status pipeline as the sidebar so the
    "broken" set is always fresh and consistent with the red dots.
    """
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    status_resp = await connection_status(authorization)
    statuses = status_resp.get("statuses", [])
    broken_ids = [
        s for s in statuses
        if s.get("status") == "disconnected"
        and s.get("error") in _BROKEN_REASONS
    ]
    if not broken_ids:
        return {"ok": True, "count": 0, "broken": []}

    # Hydrate label + branch from the project rows for the banner UI.
    pids = [s["project_id"] for s in broken_ids]
    rows = await db.cto_projects.find(
        {"user_id": user["user_id"], "project_id": {"$in": pids}},
        {"_id": 0, "project_id": 1, "name": 1, "label": 1,
         "github_owner": 1, "github_repo": 1, "branch": 1},
    ).to_list(100)
    by_pid = {r["project_id"]: r for r in rows}

    broken = []
    for s in broken_ids:
        pid = s["project_id"]
        row = by_pid.get(pid) or {}
        broken.append({
            "project_id": pid,
            "name": row.get("label") or row.get("name") or pid,
            "owner": row.get("github_owner") or s.get("owner") or "",
            "repo": row.get("github_repo") or s.get("repo") or "",
            "branch": row.get("branch") or "",
            "error": s.get("error"),
            "http_code": s.get("http_code"),
        })

    return {"ok": True, "count": len(broken), "broken": broken}


@router.post("/cleanup-delete")
async def cleanup_delete(
    body: dict,
    authorization: str = Header(None),
) -> dict:
    """Bulk-delete the caller's broken projects after re-verifying each
    is still broken (defence against a stale UI submitting a working
    project_id by mistake).

    Returns count of rows deleted + an audit_id stamped into the
    `repo_cleanup_audit` collection for traceability.
    """
    user = await current_dev(authorization)
    user_id = user["user_id"]
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    project_ids = body.get("project_ids") or []
    if not isinstance(project_ids, list) or not project_ids:
        raise HTTPException(400, "project_ids must be a non-empty list")
    if len(project_ids) > 50:
        raise HTTPException(400, "max 50 projects per cleanup batch")
    if not all(isinstance(pid, str) and pid.strip() for pid in project_ids):
        raise HTTPException(400, "project_ids must be non-empty strings")

    # Re-verify each is actually broken so a stale UI can't bulk-delete
    # a project the user just successfully re-linked in another tab.
    fresh = await connection_status(authorization)
    broken_now = {
        s["project_id"]
        for s in fresh.get("statuses", [])
        if s.get("status") == "disconnected"
        and s.get("error") in _BROKEN_REASONS
    }
    confirmed = [pid for pid in project_ids if pid in broken_now]
    if not confirmed:
        return {"ok": True, "deleted": 0, "skipped": len(project_ids),
                "reason": "no projects in submitted list are still broken"}

    # Snapshot the rows we're about to delete (for the audit trail).
    snap = await db.cto_projects.find(
        {"user_id": user_id, "project_id": {"$in": confirmed}},
        {"_id": 0},
    ).to_list(50)
    # Scrub the encrypted PAT before persisting to the audit.
    for r in snap:
        r.pop("github_token", None)

    audit_id = f"cleanup_{int(time.time())}_{user_id[:8]}"
    try:
        await db.repo_cleanup_audit.insert_one({
            "audit_id": audit_id,
            "user_id": user_id,
            "deleted_at": time.time(),
            "project_ids": confirmed,
            "snapshot": snap,
            "reason_set": list(_BROKEN_REASONS),
        })
    except Exception as e:                                # noqa: BLE001
        logger.warning("repo_cleanup_audit insert soft-failed: %r", e)

    r = await db.cto_projects.delete_many(
        {"user_id": user_id, "project_id": {"$in": confirmed}},
    )

    # Clear the connection-status cache for the deleted rows so the
    # sidebar doesn't carry stale "red" entries.
    for pid in confirmed:
        _CACHE.pop(pid, None)

    return {
        "ok": True,
        "deleted": r.deleted_count,
        "skipped": len(project_ids) - len(confirmed),
        "audit_id": audit_id,
    }
