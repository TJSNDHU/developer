"""
routers/shipwall.py
===================
Public "Ship Wall" — every task AUREM ships becomes a public card.
No auth needed to view. Developers share their ship card on X/LinkedIn.

Endpoints:
  GET  /api/aurem-dev/wall/feed          — latest 50 public ships
  GET  /api/aurem-dev/wall/user/{handle} — one developer's public ships
  POST /api/aurem-dev/wall/opt-out       — developer hides their ships
  GET  /api/aurem-dev/wall/badge/{user_id} — SVG badge for README
  GET  /api/aurem-dev/wall/card/{task_id}  — OG card data for sharing
  GET  /api/aurem-dev/wall/stats          — global stats (total ships, devs, repos)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response

from cto_services.auth import current_dev
from cto_services.db import get_db, require_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wall", tags=["Ship Wall"])

APP_URL = os.getenv("APP_URL", "https://auremcto.com")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _public_ship(task: dict, user: dict) -> dict:
    """Strips sensitive fields before serving publicly."""
    name = user.get("name") or user.get("email", "").split("@")[0] or "Developer"
    handle = user.get("github_login") or user.get("user_id", "")[:8]
    return {
        "task_id":    task.get("task_id"),
        "summary":    (task.get("result") or task.get("task", ""))[:120],
        "repo":       f"{task.get('github_owner','')}/{task.get('github_repo','')}",
        "commit_sha": (task.get("commit_sha") or "")[:7],
        "commit_url": _commit_url(task),
        "maxx_mode":  bool(task.get("maxx_mode")),
        "shipped_at": task.get("completed_at") or task.get("created_at"),
        "developer":  {"name": name, "handle": handle, "avatar": user.get("avatar_url")},
        "share_url":  f"{APP_URL}/wall/card/{task.get('task_id')}",
    }


def _commit_url(task: dict) -> Optional[str]:
    owner = task.get("github_owner")
    repo  = task.get("github_repo")
    sha   = task.get("commit_sha")
    if owner and repo and sha:
        return f"https://github.com/{owner}/{repo}/commit/{sha}"
    return None


# ── Feed ─────────────────────────────────────────────────────────────────────

@router.get("/feed")
async def ship_wall_feed(limit: int = 50) -> dict:
    """
    Public feed — last N ships across all opted-in developers.
    No auth needed. Called every 30s by the Ship Wall page.
    """
    db = get_db()
    if db is None:
        return {"ok": True, "ships": [], "total": 0}

    limit = min(max(limit, 1), 100)

    # Only show done tasks from users who haven't opted out
    cursor = db.cto_tasks.aggregate([
        {"$match": {
            "status": "done",
            "commit_sha": {"$exists": True, "$ne": None},
            "wall_hidden": {"$ne": True},
        }},
        {"$sort": {"completed_at": -1}},
        {"$limit": limit},
        {"$lookup": {
            "from": "dev_users",
            "localField": "user_id",
            "foreignField": "user_id",
            "as": "user_doc",
        }},
        {"$unwind": {"path": "$user_doc", "preserveNullAndEmptyArrays": True}},
    ])

    ships = []
    async for doc in cursor:
        user = doc.get("user_doc") or {}
        if user.get("wall_opt_out"):
            continue
        ships.append(_public_ship(doc, user))

    total = await db.cto_tasks.count_documents({
        "status": "done",
        "commit_sha": {"$exists": True, "$ne": None},
        "wall_hidden": {"$ne": True},
    })

    return {"ok": True, "ships": ships, "total": total}


# ── Single developer's wall ───────────────────────────────────────────────────

@router.get("/user/{handle}")
async def developer_wall(handle: str) -> dict:
    """Public profile wall for one developer. No auth needed."""
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")

    user = await db.dev_users.find_one(
        {"$or": [{"github_login": handle}, {"user_id": handle}]},
        {"_id": 0, "password": 0, "github_token": 0, "github_oauth_token": 0},
    )
    if not user or user.get("wall_opt_out"):
        raise HTTPException(404, "Developer not found or profile is private")

    tasks = await db.cto_tasks.find(
        {"user_id": user["user_id"], "status": "done",
         "commit_sha": {"$exists": True, "$ne": None}, "wall_hidden": {"$ne": True}},
        {"_id": 0},
    ).sort("completed_at", -1).limit(30).to_list(30)

    ships = [_public_ship(t, user) for t in tasks]
    name = user.get("name") or user.get("email", "").split("@")[0]

    return {
        "ok": True,
        "developer": {
            "name":         name,
            "handle":       handle,
            "avatar":       user.get("avatar_url"),
            "tasks_shipped": len(ships),
            "member_since": user.get("created_at"),
        },
        "ships": ships,
    }


# ── Share card data ───────────────────────────────────────────────────────────

@router.get("/card/{task_id}")
async def ship_card(task_id: str) -> dict:
    """
    Returns OG card data for a single ship.
    Used by: share modal, Twitter/X cards, LinkedIn previews.
    """
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")

    task = await db.cto_tasks.find_one(
        {"task_id": task_id, "status": "done", "wall_hidden": {"$ne": True}},
        {"_id": 0},
    )
    if not task:
        raise HTTPException(404, "Ship not found or is private")

    user = await db.dev_users.find_one(
        {"user_id": task["user_id"]},
        {"_id": 0, "name": 1, "github_login": 1, "avatar_url": 1, "wall_opt_out": 1},
    ) or {}

    if user.get("wall_opt_out"):
        raise HTTPException(404, "Developer profile is private")

    ship = _public_ship(task, user)
    tweet_text = (
        f"Just shipped to {ship['repo']} with @AUREMcto\n\n"
        f"{ship['summary']}\n\n"
        f"Commit: {ship['commit_url'] or ''}\n"
        f"#AUREM #ShippedWithAI"
    )

    return {
        "ok":         True,
        "ship":       ship,
        "tweet_text": tweet_text,
        "badge_url":  f"{APP_URL}/api/aurem-dev/wall/badge/{task['user_id']}",
    }


# ── README badge (SVG) ────────────────────────────────────────────────────────

@router.get("/badge/{user_id}")
async def readme_badge(user_id: str):
    """
    Returns an SVG badge for GitHub READMEs.
    Usage: ![Built with AUREM](https://auremcto.com/api/aurem-dev/wall/badge/USER_ID)
    """
    db = get_db()
    count = 0
    if db is not None:
        count = await db.cto_tasks.count_documents(
            {"user_id": user_id, "status": "done",
             "commit_sha": {"$exists": True, "$ne": None}}
        )

    label = "Built with AUREM"
    value = f"{count} ships"
    lw = len(label) * 6 + 16
    rw = len(value) * 6 + 16
    total = lw + rw

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{total}" height="20" rx="3"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{lw}" height="20" fill="#555"/>
    <rect x="{lw}" width="{rw}" height="20" fill="#7F77DD"/>
    <rect width="{total}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
    <text x="{lw//2}" y="15" fill="#010101" fill-opacity=".3">{label}</text>
    <text x="{lw//2}" y="14">{label}</text>
    <text x="{lw + rw//2}" y="15" fill="#010101" fill-opacity=".3">{value}</text>
    <text x="{lw + rw//2}" y="14">{value}</text>
  </g>
</svg>"""

    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "max-age=3600", "Access-Control-Allow-Origin": "*"},
    )


# ── Global stats ──────────────────────────────────────────────────────────────

@router.get("/stats")
async def wall_stats() -> dict:
    """Global numbers shown on the Ship Wall page."""
    db = get_db()
    if db is None:
        return {"total_ships": 0, "total_devs": 0, "total_repos": 0}

    pipeline = [
        {"$match": {"status": "done", "commit_sha": {"$exists": True, "$ne": None}}},
        {"$group": {
            "_id": None,
            "total_ships": {"$sum": 1},
            "total_devs":  {"$addToSet": "$user_id"},
            "total_repos": {"$addToSet": {
                "$concat": [
                    {"$ifNull": ["$github_owner", ""]}, "/",
                    {"$ifNull": ["$github_repo",  ""]}
                ]
            }},
        }},
    ]
    result = await db.cto_tasks.aggregate(pipeline).to_list(1)
    if not result:
        return {"total_ships": 0, "total_devs": 0, "total_repos": 0}

    r = result[0]
    return {
        "total_ships": r.get("total_ships", 0),
        "total_devs":  len(r.get("total_devs", [])),
        "total_repos": len(r.get("total_repos", [])),
    }


# ── Opt out ───────────────────────────────────────────────────────────────────

@router.post("/opt-out")
async def wall_opt_out(authorization: str = Header(None)) -> dict:
    """Developer hides all their ships from the public wall."""
    me = await current_dev(authorization)
    db = require_db()
    await db.dev_users.update_one(
        {"user_id": me["user_id"]},
        {"$set": {"wall_opt_out": True}},
    )
    return {"ok": True, "message": "Your ships are now hidden from the public wall."}


@router.post("/opt-in")
async def wall_opt_in(authorization: str = Header(None)) -> dict:
    """Developer makes their ships public again."""
    me = await current_dev(authorization)
    db = require_db()
    await db.dev_users.update_one(
        {"user_id": me["user_id"]},
        {"$unset": {"wall_opt_out": ""}},
    )
    return {"ok": True, "message": "Your ships are now public on the wall."}
