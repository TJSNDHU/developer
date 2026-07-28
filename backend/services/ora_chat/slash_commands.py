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
            {"cmd": "/loop-stats [id]",        "desc": "Real per-phase durations for a loop run (defaults to your latest)"},
            {"cmd": "/rule add|list|delete|on|off|report", "desc": "Persistent correction rules (Phase 1, shadow-first)"},
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


# ─── Loop execution stats (Iter 275) ─────────────────────────────
# Deterministic aggregation over `loop_run_log` + `loop_sessions` so
# any question about "how long did the fix take / plan phase / verify"
# is answered from real audit rows, not a guess.
async def _loop_stats(ctx: dict, args: str) -> dict:
    """/loop-stats [loop_id] — real per-phase durations for a loop run.

    If `loop_id` is omitted, uses the caller's most-recent loop run.
    Returns start/end timestamps, per-phase elapsed seconds, and total
    duration. All values are computed from the `created_at` field of
    `loop_run_log` audit rows written by `loop_engine.py` at every
    phase boundary."""
    db = get_db()
    if db is None:
        return {"ok": False, "error": "database_unavailable"}
    loop_id = (args or "").strip() or None

    # Resolve loop_id if not passed.
    if not loop_id:
        user_id = (ctx or {}).get("user_id")
        query = {"user_id": user_id} if user_id else {}
        sess = await db.loop_sessions.find_one(
            query, {"_id": 0, "loop_id": 1, "created_at": 1},
            sort=[("created_at", -1)],
        )
        if not sess:
            return {"ok": False, "error": "no_loop_runs_found",
                     "hint": "No loop runs for this user yet. Kick "
                              "one off via LOOP mode in the chat, then "
                              "try /loop-stats again."}
        loop_id = sess["loop_id"]

    # Pull audit rows in time order.
    rows: list[dict] = []
    cursor = db.loop_run_log.find(
        {"loop_id": loop_id},
        {"_id": 0, "phase": 1, "kind": 1, "verdict": 1, "created_at": 1},
    ).sort("created_at", 1)
    async for r in cursor:
        rows.append(r)
    if not rows:
        # Fall back to loop_sessions.last_event / state if no audit rows.
        sess = await db.loop_sessions.find_one(
            {"loop_id": loop_id},
            {"_id": 0, "created_at": 1, "updated_at": 1, "state": 1,
             "phase": 1, "user_id": 1},
        )
        if not sess:
            return {"ok": False, "error": "loop_not_found",
                     "hint": f"No loop with id '{loop_id}' — check the id."}
        return {
            "ok": True,
            "command": "loop-stats",
            "metric":  f"Loop {loop_id} (audit rows unavailable)",
            "value": {
                "loop_id":            loop_id,
                "phase_durations_s":  {},
                "start_ts":           sess.get("created_at"),
                "end_ts":             sess.get("updated_at"),
                "current_state":      sess.get("state"),
                "current_phase":      sess.get("phase"),
                "total_duration_s":   round(
                    float((sess.get("updated_at") or 0) -
                          (sess.get("created_at") or 0)), 2),
                "audit_rows":         0,
                "note":               "loop_run_log had no rows for this loop "
                                       "— aggregation done from loop_sessions "
                                       "timestamps only. Per-phase breakdown "
                                       "not available.",
            },
        }

    # Timestamps → ISO parseable → epoch seconds.
    from datetime import datetime
    def _parse_iso(s: str) -> float:
        # Backend writes datetime.now(UTC).isoformat() so `s` is ISO-8601.
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:                                     # noqa: BLE001
            return 0.0

    ts = [_parse_iso(r["created_at"]) for r in rows]
    start_epoch = ts[0]
    end_epoch   = ts[-1]

    # Phase durations: elapsed between rows within the same phase +
    # the transition into it. Approach: bucket each interval to the
    # phase of its OPENING row (the one that emitted the event).
    phase_totals: dict[str, float] = {}
    for i in range(len(rows) - 1):
        ph = (rows[i].get("phase") or "unknown").lower()
        dt = max(0.0, ts[i + 1] - ts[i])
        phase_totals[ph] = phase_totals.get(ph, 0.0) + dt
    # Round for display.
    phase_totals = {k: round(v, 2) for k, v in phase_totals.items()}

    # Also report the final phase reached + its verdict for context.
    final_row = rows[-1]

    return {
        "ok": True,
        "command": "loop-stats",
        "metric":  f"Loop {loop_id} execution breakdown",
        "value": {
            "loop_id":           loop_id,
            "audit_rows":        len(rows),
            "start_ts":          rows[0]["created_at"],
            "end_ts":            rows[-1]["created_at"],
            "total_duration_s":  round(end_epoch - start_epoch, 2),
            "phase_durations_s": phase_totals,
            "final_phase":       final_row.get("phase"),
            "final_verdict":     final_row.get("verdict"),
            "note":              "Per-phase durations are gaps between "
                                  "sequential loop_run_log entries — real "
                                  "audit-row timing, not synthetic.",
        },
    }


# ─── Persistent Correction Rules (Iter 333 · Phase 1) ────────────
# WRITE exception to this module's read-only rule: founder-approved
# Phase 1 design mandates rule management via manual slash command.
# All writes are scoped to the CALLER's own docs (user_id from the
# authenticated JWT ctx — never from the command string).
_RULE_USAGE = [
    {"cmd": "/rule add <instruction> [paths: src/*, *.py]",
     "desc": "Add a persistent correction rule"},
    {"cmd": "/rule list",            "desc": "List your rules for the active project"},
    {"cmd": "/rule delete <id>",     "desc": "Delete a rule by id"},
    {"cmd": "/rule on | /rule off",  "desc": "Enforce mode toggle (default OFF = shadow)"},
    {"cmd": "/rule report",          "desc": "Match metrics (hits, loops affected)"},
]


async def _rule(ctx: dict, args: str) -> dict:
    from services import correction_rules as cr
    db = get_db()
    if db is None:
        return {"ok": False, "error": "database_unavailable"}
    user_id = (ctx or {}).get("user_id")
    if not user_id:
        return {"ok": False, "error": "unauthenticated"}
    project_id = await cr.resolve_active_project(db, user_id)
    sub, _, rest = (args or "").strip().partition(" ")
    sub, rest = sub.lower(), rest.strip()

    if sub == "add":
        instruction, globs = cr.parse_add_args(rest)
        res = await cr.add_rule(db, user_id, project_id, instruction, globs)
        if not res.get("ok"):
            return {"ok": False, "command": "rule", **res}
        r = res["rule"]
        return {"ok": True, "command": "rule",
                "metric": "Correction rule added (shadow mode until /rule on)",
                "value": {"rule_id": r["rule_id"],
                           "instruction": r["instruction"],
                           "applies_to_paths": r["applies_to_paths"] or ["<all files>"],
                           "project_id": project_id}}

    if sub == "list":
        rules = await cr.list_rules(db, user_id, project_id)
        enforce = await cr.get_enforce(db, user_id, project_id)
        return {"ok": True, "command": "rule",
                "metric": f"Correction rules ({'ENFORCE' if enforce else 'shadow'} mode)",
                "value": [{"rule_id": r["rule_id"],
                            "instruction": r["instruction"],
                            "paths": r.get("applies_to_paths") or ["<all>"],
                            "hits": r.get("hits", 0)} for r in rules]
                          or "No rules yet — /rule add <instruction>"}

    if sub == "delete":
        if not rest:
            return {"ok": False, "command": "rule", "error": "missing_rule_id",
                    "hint": "Usage: /rule delete <rule_id>"}
        deleted = await cr.delete_rule(db, user_id, rest.split()[0])
        return {"ok": deleted, "command": "rule",
                "metric": "Rule deleted" if deleted else "Rule not found",
                "value": rest.split()[0]}

    if sub in ("on", "off"):
        await cr.set_enforce(db, user_id, project_id, sub == "on")
        return {"ok": True, "command": "rule",
                "metric": "Enforce mode " + ("ON — rules now injected into "
                          "executor prompts (max 10)" if sub == "on"
                          else "OFF — shadow mode (matches logged only)"),
                "value": {"enforce": sub == "on", "project_id": project_id}}

    if sub == "report":
        rep = await cr.rule_report(db, user_id, project_id)
        return {"ok": True, "command": "rule",
                "metric": "Correction rule match report", "value": rep}

    return {"ok": True, "command": "rule",
            "metric": "Usage — persistent correction rules",
            "value": _RULE_USAGE}


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
    "loop-stats":             _loop_stats,
    "rule":                   _rule,
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
