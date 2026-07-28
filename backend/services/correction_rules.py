"""
services/correction_rules.py — Iter 333 · Phase 1: Persistent Correction Rules.

Founder-locked design (binding corrections, see PRD/CHANGELOG):
  - NO LLM correction-detection — rules are created ONLY via the
    manual `/rule` slash command.
  - Path scoping via `applies_to_paths` (fnmatch globs; empty = all).
  - Max 10 rules injected per prompt (MAX_RULES_PER_PROMPT).
  - Per-project enforce toggle, DEFAULT OFF → shadow mode: matches are
    logged to `correction_rule_events` + surfaced in narration, but
    NOT injected into the executor prompt.
  - Instrumented success metric: every match writes an event row +
    bumps per-rule hit counters; `/rule report` aggregates them.

Collections:
  correction_rules          — the rules themselves (user+project scoped)
  correction_rule_settings  — {user_id, project_id, enforce: bool}
  correction_rule_events    — one row per loop-phase match (the metric)

Fail-open discipline: loop_engine callers wrap every call in
try/except — a rules failure must NEVER block a loop.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Optional

logger = logging.getLogger(__name__)

MAX_RULES_PER_PROJECT = 50
MAX_RULES_PER_PROMPT = 10
MAX_INSTRUCTION_LEN = 300
MAX_PATH_GLOBS = 10

_PATHS_RE = re.compile(r"\bpaths:\s*(.+)$", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def resolve_active_project(db, user_id: str) -> Optional[str]:
    """Most-recent cto_projects row — same server-side resolution the
    suggestions router uses (client never passes project_id)."""
    proj = await db.cto_projects.find_one(
        {"user_id": user_id},
        {"_id": 0, "project_id": 1},
        sort=[("created_at", -1)],
    )
    return (proj or {}).get("project_id")


def parse_add_args(rest: str) -> tuple[str, list[str]]:
    """Split `/rule add <instruction> [paths: g1, g2]` into
    (instruction, applies_to_paths)."""
    rest = (rest or "").strip()
    globs: list[str] = []
    m = _PATHS_RE.search(rest)
    if m:
        globs = [g.strip() for g in m.group(1).split(",") if g.strip()]
        rest = rest[:m.start()].strip()
    return rest, globs[:MAX_PATH_GLOBS]


async def add_rule(db, user_id: str, project_id: Optional[str],
                   instruction: str,
                   applies_to_paths: Optional[list] = None) -> dict:
    instruction = (instruction or "").strip()
    if not instruction:
        return {"ok": False, "error": "empty_instruction",
                "hint": "Usage: /rule add <instruction> [paths: src/*, *.py]"}
    if len(instruction) > MAX_INSTRUCTION_LEN:
        return {"ok": False, "error": "instruction_too_long",
                "hint": f"Max {MAX_INSTRUCTION_LEN} chars."}
    globs = [str(g)[:200] for g in (applies_to_paths or [])][:MAX_PATH_GLOBS]
    n = await db.correction_rules.count_documents(
        {"user_id": user_id, "project_id": project_id})
    if n >= MAX_RULES_PER_PROJECT:
        return {"ok": False, "error": "rule_limit_reached",
                "hint": f"Max {MAX_RULES_PER_PROJECT} rules per project — "
                        "delete one first (/rule list, /rule delete <id>)."}
    doc = {
        "rule_id":          uuid.uuid4().hex[:12],
        "user_id":          user_id,
        "project_id":       project_id,
        "instruction":      instruction,
        "applies_to_paths": globs,
        "active":           True,
        "source":           "slash_command",
        "hits":             0,
        "last_hit_at":      None,
        "created_at":       _now_iso(),
    }
    await db.correction_rules.insert_one(dict(doc))
    doc.pop("_id", None)
    return {"ok": True, "rule": doc}


async def list_rules(db, user_id: str, project_id: Optional[str]) -> list:
    cur = db.correction_rules.find(
        {"user_id": user_id, "project_id": project_id}, {"_id": 0},
    ).sort("created_at", 1)
    return await cur.to_list(length=MAX_RULES_PER_PROJECT)


async def delete_rule(db, user_id: str, rule_id: str) -> bool:
    res = await db.correction_rules.delete_one(
        {"user_id": user_id, "rule_id": rule_id})
    return bool(getattr(res, "deleted_count", 0))


async def get_enforce(db, user_id: str, project_id: Optional[str]) -> bool:
    """Per-project enforce toggle. DEFAULT False = shadow mode."""
    doc = await db.correction_rule_settings.find_one(
        {"user_id": user_id, "project_id": project_id}, {"_id": 0})
    return bool((doc or {}).get("enforce", False))


async def set_enforce(db, user_id: str, project_id: Optional[str],
                      enforce: bool) -> None:
    await db.correction_rule_settings.update_one(
        {"user_id": user_id, "project_id": project_id},
        {"$set": {"enforce": bool(enforce), "updated_at": _now_iso()}},
        upsert=True,
    )


async def load_active_rules(db, user_id: str,
                            project_id: Optional[str]) -> list:
    cur = db.correction_rules.find(
        {"user_id": user_id, "project_id": project_id, "active": True},
        {"_id": 0},
    ).sort("created_at", 1)
    return await cur.to_list(length=MAX_RULES_PER_PROJECT)


def match_rules(rules: list, paths: list) -> list:
    """Pure function: which rules apply to which of these file paths.
    Empty `applies_to_paths` on a rule = applies to every path.
    Returns [{rule, matched_paths}], capped at MAX_RULES_PER_PROMPT
    (oldest rules win — deterministic)."""
    out = []
    clean_paths = [str(p).strip() for p in (paths or []) if str(p).strip()]
    if not clean_paths:
        return out
    for rule in (rules or []):
        globs = rule.get("applies_to_paths") or []
        if not globs:
            matched = list(clean_paths)
        else:
            matched = [p for p in clean_paths
                       if any(fnmatch(p, g) for g in globs)]
        if matched:
            out.append({"rule": rule, "matched_paths": matched})
        if len(out) >= MAX_RULES_PER_PROMPT:
            break
    return out


def build_rules_block(matches: list) -> str:
    """Prompt block for ENFORCE mode only. Empty string when nothing
    matched, otherwise ends with a blank line so it slots cleanly
    between plan and file sections of the executor task text."""
    if not matches:
        return ""
    lines = ["PERSISTENT CORRECTION RULES (founder-set — MUST follow):"]
    for i, m in enumerate(matches[:MAX_RULES_PER_PROMPT], 1):
        lines.append(f"{i}. {m['rule']['instruction']}")
    return "\n".join(lines) + "\n\n"


async def record_rule_events(db, *, loop_id: str, user_id: str,
                             project_id: Optional[str], phase: str,
                             matches: list, mode: str) -> None:
    """The Phase 1 instrumented success metric: one event row per
    matched rule per loop-phase + per-rule hit counters."""
    now = _now_iso()
    for m in matches:
        rule = m["rule"]
        try:
            await db.correction_rule_events.insert_one({
                "loop_id":       loop_id,
                "user_id":       user_id,
                "project_id":    project_id,
                "rule_id":       rule["rule_id"],
                "instruction":   rule["instruction"][:120],
                "phase":         phase,
                "mode":          mode,
                "matched_paths": m["matched_paths"][:20],
                "ts":            now,
            })
            await db.correction_rules.update_one(
                {"rule_id": rule["rule_id"]},
                {"$inc": {"hits": 1}, "$set": {"last_hit_at": now}},
            )
        except Exception as e:                              # noqa: BLE001
            logger.warning("correction_rule_events write failed: %r", e)


async def rule_report(db, user_id: str,
                      project_id: Optional[str]) -> dict:
    """Aggregate the metric: per-rule hits, loops affected, mode split."""
    rules = await list_rules(db, user_id, project_id)
    rows = []
    total_events = 0
    loops: set = set()
    for r in rules:
        events = await db.correction_rule_events.find(
            {"rule_id": r["rule_id"]}, {"_id": 0, "loop_id": 1, "mode": 1},
        ).to_list(length=1000)
        total_events += len(events)
        loops.update(e["loop_id"] for e in events)
        rows.append({
            "rule_id":     r["rule_id"],
            "instruction": r["instruction"][:80],
            "hits":        r.get("hits", 0),
            "last_hit_at": r.get("last_hit_at"),
        })
    return {
        "rule_count":     len(rules),
        "total_matches":  total_events,
        "loops_affected": len(loops),
        "rules":          rows,
    }
