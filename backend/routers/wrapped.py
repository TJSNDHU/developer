"""
routers/wrapped.py
==================
ORA Wrapped — monthly developer stats. Shareable card.

Inspired by Spotify Wrapped. Auto-generated every month end.
Developer gets a "Your month with ORA" summary card they can share on X/LinkedIn.

Endpoints:
  GET /api/aurem-dev/wrapped/me          — current user's wrapped stats
  GET /api/aurem-dev/wrapped/me/image    — returns shareable text card data
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Header

from cto_services.auth import current_dev
from cto_services.db import require_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wrapped", tags=["ORA Wrapped"])


def _date_range(period: str) -> tuple[float, float]:
    """Returns (start_ts, end_ts) for 'this_month', 'last_month', or 'all'."""
    now = datetime.now(timezone.utc)
    if period == "last_month":
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_end = first_this - timedelta(seconds=1)
        first_last = last_month_end.replace(day=1, hour=0, minute=0, second=0)
        return first_last.timestamp(), last_month_end.timestamp()
    if period == "this_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp(), now.timestamp()
    # all time
    return 0.0, now.timestamp()


@router.get("/me")
async def my_wrapped(
    period: str = "this_month",
    authorization: str = Header(None),
) -> dict:
    """
    Returns the developer's ORA Wrapped stats for the given period.
    period: 'this_month' | 'last_month' | 'all'
    """
    me = await current_dev(authorization)
    db = require_db()
    user_id = me["user_id"]

    start_ts, end_ts = _date_range(period)

    tasks = await db.cto_tasks.find(
        {
            "user_id":    user_id,
            "created_at": {"$gte": start_ts, "$lte": end_ts},
        },
        {"_id": 0},
    ).to_list(500)

    if not tasks:
        return {
            "ok": True, "period": period,
            "stats": _empty_stats(),
            "has_data": False,
        }

    done     = [t for t in tasks if t.get("status") == "done"]
    failed   = [t for t in tasks if t.get("status") == "failed"]
    maxx     = [t for t in done  if t.get("maxx_mode")]

    # Most used mode
    mode_counts: dict[str, int] = {}
    for t in tasks:
        src = t.get("source", "")
        if "handoff" in src:
            mode_counts["D"] = mode_counts.get("D", 0) + 1
        elif t.get("maxx_mode"):
            mode_counts["C+Maxx"] = mode_counts.get("C+Maxx", 0) + 1
        else:
            mode_counts["C"] = mode_counts.get("C", 0) + 1
    top_mode = max(mode_counts, key=mode_counts.get) if mode_counts else "C"

    # Repos touched
    repos = {
        f"{t.get('github_owner','')}/{t.get('github_repo','')}"
        for t in done
        if t.get("github_owner")
    }

    # Estimate hours saved: each task = ~45 min of manual work
    hours_saved = round(len(done) * 0.75, 1)

    # Streak: consecutive days with at least one ship
    ship_days = sorted({
        datetime.fromtimestamp(t["completed_at"], tz=timezone.utc).date()
        for t in done
        if t.get("completed_at")
    })
    streak = _calc_streak(ship_days)

    # Claude corrections (Maxx mode catches)
    corrections = sum(
        1 for t in maxx
        if not t.get("review_passed", True)
    )

    label = _period_label(period)
    user = await db.dev_users.find_one({"user_id": user_id}, {"_id": 0, "name": 1, "github_login": 1}) or {}
    dev_name = user.get("name") or user.get("github_login") or "Developer"

    stats = {
        "tasks_shipped":    len(done),
        "tasks_failed":     len(failed),
        "repos_touched":    len(repos),
        "hours_saved":      hours_saved,
        "maxx_tasks":       len(maxx),
        "claude_corrections": corrections,
        "top_mode":         top_mode,
        "ship_streak_days": streak,
        "period_label":     label,
        "developer_name":   dev_name,
    }

    return {
        "ok":      True,
        "period":  period,
        "stats":   stats,
        "has_data": len(done) > 0,
        "share_text": _share_text(stats),
    }


def _empty_stats() -> dict:
    return {
        "tasks_shipped": 0, "tasks_failed": 0, "repos_touched": 0,
        "hours_saved": 0.0, "maxx_tasks": 0, "claude_corrections": 0,
        "top_mode": "—", "ship_streak_days": 0,
        "period_label": "this month", "developer_name": "Developer",
    }


def _period_label(period: str) -> str:
    now = datetime.now(timezone.utc)
    if period == "last_month":
        first = now.replace(day=1) - timedelta(days=1)
        return first.strftime("%B %Y")
    if period == "this_month":
        return now.strftime("%B %Y")
    return "All time"


def _calc_streak(ship_days: list) -> int:
    """Counts consecutive days ending today or yesterday."""
    if not ship_days:
        return 0
    today = datetime.now(timezone.utc).date()
    streak = 0
    current = today
    for day in reversed(ship_days):
        if day == current or day == current - timedelta(days=1):
            streak += 1
            current = day
        else:
            break
    return streak


def _share_text(s: dict) -> str:
    lines = [
        f"My {s['period_label']} with @AUREMcto ORA:",
        "",
        f"Ships: {s['tasks_shipped']} tasks pushed to GitHub",
        f"Time saved: ~{s['hours_saved']} hours",
        f"Repos touched: {s['repos_touched']}",
    ]
    if s["ship_streak_days"] > 2:
        lines.append(f"Streak: {s['ship_streak_days']} days straight")
    if s["claude_corrections"] > 0:
        lines.append(f"Claude caught {s['claude_corrections']} issues before commit")
    lines += ["", "#AUREM #ShipWithAI #BuildInPublic"]
    return "\n".join(lines)
