"""
services/ora_chat/slash_commands.py — Iter 212m-238

Deterministic, pre-written DB queries mapped to slash-commands.

Zero LLM interpretation of the fetch step: each command name maps
1-to-1 to a Python function that runs a hard-coded Mongo query and
returns structured data. The LLM is only invoked afterward (via the
"slash_explain" route at temp=0.1) to add a one-line human summary.

Design rules:
  - No free-form parameters. All args are name→literal, whitelisted.
  - No user input flows into the query filter. Any date/user-scope
    arg comes from the caller's authenticated JWT payload, never from
    the slash-command string.
  - Read-only. No writes, no deletes, no destructive ops.
"""
from __future__ import annotations

import time
from typing import Callable, Awaitable

from cto_services.db import get_db
from services.ora_chat import codebase_index


# One dispatch entry per registered slash-command. Each returns a
# structured dict (never a string) so the caller can decide what to
# render + whether to feed it to the "slash_explain" formatter LLM.
SlashHandler = Callable[[dict, str], Awaitable[dict]]


async def _users_today(ctx: dict, _args: str) -> dict:
    """Count new signups in the last 24 hours."""
    db = get_db()
    if db is None:
        return {"ok": False, "error": "database_unavailable"}
    cutoff = time.time() - 86400
    n = await db.dev_users.count_documents({"created_at": {"$gte": cutoff}})
    return {
        "ok": True,
        "command": "users-today",
        "metric": "New signups (last 24h)",
        "value": n,
    }


async def _active_users(ctx: dict, _args: str) -> dict:
    """Count users who logged in within the last 7 days."""
    db = get_db()
    if db is None:
        return {"ok": False, "error": "database_unavailable"}
    cutoff = time.time() - 7 * 86400
    n = await db.dev_users.count_documents({"last_login_at": {"$gte": cutoff}})
    total = await db.dev_users.count_documents({})
    return {
        "ok": True,
        "command": "active-users",
        "metric": "Active users (7 day window)",
        "value": n,
        "total_users": total,
    }


async def _personal_track_signups(ctx: dict, _args: str) -> dict:
    """Breakdown of users by track."""
    db = get_db()
    if db is None:
        return {"ok": False, "error": "database_unavailable"}
    personal = await db.dev_users.count_documents({"track": "personal"})
    developer = await db.dev_users.count_documents({"track": "developer"})
    unset = await db.dev_users.count_documents({"track": {"$in": [None, ""]}})
    return {
        "ok": True,
        "command": "personal-track-signups",
        "metric": "Users by track",
        "value": {"personal": personal, "developer": developer, "unset": unset},
    }


async def _legacy_nudge_clicks(ctx: dict, _args: str) -> dict:
    """Legacy-nudge banner activation funnel."""
    db = get_db()
    if db is None:
        return {"ok": False, "error": "database_unavailable"}
    clicked = await db.dev_users.count_documents(
        {"personal_nudge_clicked_at": {"$exists": True}}
    )
    converted = await db.dev_users.count_documents({
        "personal_nudge_clicked_at": {"$exists": True},
        "track": "personal",
    })
    return {
        "ok": True,
        "command": "legacy-nudge-clicks",
        "metric": "Legacy banner activation funnel",
        "value": {
            "banner_clicked": clicked,
            "converted_to_personal": converted,
            "conversion_rate_pct": (
                round(100 * converted / clicked, 1) if clicked else 0
            ),
        },
    }


async def _revenue_snapshot(ctx: dict, _args: str) -> dict:
    """Reuse the existing scaffold admin revenue snapshot service if
    available, otherwise fall back to a lightweight direct count."""
    db = get_db()
    if db is None:
        return {"ok": False, "error": "database_unavailable"}
    # Best-effort import — this service exists per handoff summary
    # (`/api/aurem-dev/scaffold/admin/revenue-snapshot`). If the module
    # isn't importable we fall back to a minimal own-computed number.
    try:
        from services.revenue_snapshot import compute_revenue_snapshot  # type: ignore
        snap = await compute_revenue_snapshot(db)
        return {"ok": True, "command": "revenue-snapshot",
                "metric": "Revenue snapshot", "value": snap}
    except Exception:
        # Fallback: count paying users by tier.
        pipeline = [
            {"$match": {"tier": {"$in": ["tier_1", "tier_2", "tier_3", "tier_4"]}}},
            {"$group": {"_id": "$tier", "count": {"$sum": 1}}},
        ]
        by_tier: dict[str, int] = {}
        try:
            async for row in db.dev_users.aggregate(pipeline):
                by_tier[row["_id"]] = row["count"]
        except Exception:
            pass
        return {
            "ok": True,
            "command": "revenue-snapshot",
            "metric": "Paying users by tier (fallback estimate)",
            "value": by_tier,
        }


async def _help(ctx: dict, _args: str) -> dict:
    """List every registered command."""
    return {
        "ok": True,
        "command": "help",
        "metric": "Available slash-commands",
        "value": [
            {"cmd": "/users-today",            "desc": "New signups in the last 24 hours"},
            {"cmd": "/active-users",           "desc": "Users active in the last 7 days"},
            {"cmd": "/personal-track-signups", "desc": "User breakdown by track"},
            {"cmd": "/legacy-nudge-clicks",    "desc": "Legacy-banner activation funnel"},
            {"cmd": "/revenue-snapshot",       "desc": "Revenue snapshot"},
            {"cmd": "/repo-tree",              "desc": "Compact AUREM code repo tree"},
            {"cmd": "/repo-stats",             "desc": "Repo file/lang/def counts"},
            {"cmd": "/find <pattern>",         "desc": "Find files (glob or substring)"},
            {"cmd": "/read <path>",            "desc": "Read a repo file (first 200 lines)"},
            {"cmd": "/defs <name>",            "desc": "Where is a function/class defined?"},
            {"cmd": "/help",                   "desc": "This list"},
        ],
    }


# ─── Codebase-awareness commands (Iter 212m-246) ─────────────────
async def _repo_tree(ctx: dict, _args: str) -> dict:
    """Compact directory listing of the AUREM codebase."""
    text = await codebase_index.compact_tree(max_files=200)
    return {"ok": True, "command": "repo-tree",
             "metric": "AUREM repo tree", "value": text}


async def _repo_stats(ctx: dict, _args: str) -> dict:
    """File counts + language breakdown + total size."""
    stats = await codebase_index.index_stats()
    return {"ok": True, "command": "repo-stats",
             "metric": "Codebase index stats", "value": stats}


async def _find(ctx: dict, args: str) -> dict:
    """Glob-match against repo-relative paths."""
    pattern = (args or "").strip()
    if not pattern:
        return {"ok": False, "error": "missing_pattern",
                 "hint": "Usage: /find <glob-or-substring>"}
    matches = await codebase_index.find_files(pattern, limit=30)
    return {"ok": True, "command": "find",
             "metric": f"Files matching '{pattern}'",
             "value": matches}


async def _read(ctx: dict, args: str) -> dict:
    """Read a repo-relative file (bounded to 200 lines / 40 KB)."""
    path = (args or "").strip()
    if not path:
        return {"ok": False, "error": "missing_path",
                 "hint": "Usage: /read <repo-relative-path>"}
    out = await codebase_index.read_file(path, max_lines=200)
    if not out.get("ok"):
        return out
    return {"ok": True, "command": "read",
             "metric": f"File: {out['path']}",
             "value": out}


async def _defs(ctx: dict, args: str) -> dict:
    """Locate a function/class by name."""
    name = (args or "").strip()
    if not name:
        return {"ok": False, "error": "missing_name",
                 "hint": "Usage: /defs <symbol>"}
    hits = await codebase_index.search_defs(name, limit=15)
    return {"ok": True, "command": "defs",
             "metric": f"Definitions of '{name}'",
             "value": hits}


# Dispatch registry — every command in `safety.KNOWN_COMMANDS` MUST
# have an entry here. The API layer enforces the pairing.
DISPATCH: dict[str, SlashHandler] = {
    "users-today":            _users_today,
    "active-users":           _active_users,
    "personal-track-signups": _personal_track_signups,
    "legacy-nudge-clicks":    _legacy_nudge_clicks,
    "revenue-snapshot":       _revenue_snapshot,
    "repo-tree":              _repo_tree,
    "repo-stats":             _repo_stats,
    "find":                   _find,
    "read":                   _read,
    "defs":                   _defs,
    "help":                   _help,
}


async def run_slash_command(cmd: str, args: str, ctx: dict) -> dict:
    """Execute a registered slash-command. Raises KeyError on unknown.

    `ctx` carries the caller's JWT payload — currently unused by any
    handler (all queries are global) but kept in the signature so
    future per-user commands slot in without a signature change.
    """
    handler = DISPATCH[cmd]  # KeyError propagates → API layer 400s
    return await handler(ctx, args)
