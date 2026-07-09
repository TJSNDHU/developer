"""
services/scan_fix_quota.py — Iter 212m-190

Task-quota gating for Developer-Tool scan fixes (Vanguard Scan,
Health Scan, Security Scan, Bug Hunt).

RULE: 1 issue fixed = 1 task deducted. No severity-based pricing.
Gate by TOOL and FEATURE (bulk fix), never by severity.

  free    → scans only, no fixes
  starter → fix vanguard-scan only, no bulk
  pro     → fix vanguard-scan + health-scan, no bulk
  team    → fix all 4 tools, bulk fix allowed
  founder → everything, unlimited

Deduction is recorded ONLY for successful fixes via
`record_scan_fixes()` — callers must invoke it per-success so a
failed fix never burns a task. Usage rolls into the same monthly
task meter as chat tasks (services/usage.py adds `scan_fix_usage`
counts into `tasks_this_month`).
"""
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import HTTPException

from cto_services.db import require_db
from services.usage import get_usage

ALL_FIX_TOOLS = frozenset({
    "vanguard-scan", "health-scan", "security-scan", "bug-hunt",
})

FIX_TOOLS_BY_TIER: dict[str, frozenset] = {
    "free":    frozenset(),
    "starter": frozenset({"vanguard-scan"}),
    "pro":     frozenset({"vanguard-scan", "health-scan"}),
    "team":    ALL_FIX_TOOLS,
    "founder": ALL_FIX_TOOLS,
}

BULK_FIX_TIERS = frozenset({"team", "founder"})


def month_bucket() -> str:
    n = datetime.now(timezone.utc)
    return f"{n.year:04d}-{n.month:02d}"


async def get_fix_quota(user: dict) -> dict:
    """Task-quota snapshot for the fix surfaces. `tasks_remaining` is
    None for unlimited (founder) accounts."""
    usage = await get_usage(user["user_id"])
    tier = usage["tier"]
    return {
        "tier":               tier,
        "fix_tools":          sorted(FIX_TOOLS_BY_TIER.get(tier, frozenset())),
        "bulk_fix":           tier in BULK_FIX_TIERS,
        "monthly_task_limit": usage["monthly_task_cap"],
        "tasks_used":         usage["tasks_this_month"],
        "tasks_remaining":    usage["tasks_remaining"],
        "is_unlimited":       usage["is_unlimited"],
    }


async def assert_can_fix(user: dict, tool: str, count: int = 1) -> dict:
    """Gate a fix request BEFORE any work runs. Returns the quota dict.

    Raises:
      400 unknown_tool
      403 fix_not_available_on_tier — tool not in the tier's fix set
      403 bulk_fix_not_available    — count > 1 on a non-Team tier
      402 insufficient_tasks        — count > tasks remaining this month
    """
    if tool not in ALL_FIX_TOOLS:
        raise HTTPException(400, {"error": "unknown_tool", "tool": tool})
    q = await get_fix_quota(user)
    tier = q["tier"]
    if tool not in q["fix_tools"]:
        raise HTTPException(403, {
            "error": "fix_not_available_on_tier",
            "tier": tier, "tool": tool,
            "message": (
                "Fixes aren't available on the Free plan — you can run "
                "scans and view findings. Upgrade to fix issues."
                if tier == "free" else
                f"Fixing {tool.replace('-', ' ')} findings isn't included "
                f"in the {tier.title()} plan. Upgrade to unlock it."
            ),
            "upgrade_url": "/settings#pricing",
        })
    if count > 1 and not q["bulk_fix"]:
        raise HTTPException(403, {
            "error": "bulk_fix_not_available",
            "tier": tier,
            "message": ("Bulk fix is a Team-plan feature. Fix issues "
                        "individually or upgrade to Team."),
            "upgrade_url": "/settings#pricing",
        })
    remaining = q["tasks_remaining"]
    if remaining is not None and count > remaining:
        raise HTTPException(402, {
            "error": "insufficient_tasks",
            "remaining": remaining, "needed": count,
            "monthly_task_limit": q["monthly_task_limit"],
            "message": (
                f"You have {remaining} tasks left this month — not enough "
                f"for {count} fixes. Upgrade or fix issues individually."
            ),
            "upgrade_url": "/settings#pricing",
        })
    return q


async def record_scan_fixes(user_id: str, tool: str, count: int = 1) -> None:
    """Atomically deduct `count` tasks. Call ONLY for fixes that
    actually succeeded — never pre-deduct."""
    if count <= 0:
        return
    db = require_db()
    await db.scan_fix_usage.update_one(
        {"user_id": user_id, "month": month_bucket()},
        {"$inc": {"count": int(count), f"by_tool.{tool}": int(count)},
         "$set": {"updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
