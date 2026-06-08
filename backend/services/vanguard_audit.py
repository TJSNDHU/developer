"""
services/vanguard_audit.py
==========================
Iter 112 — Mongo audit log for every Vanguard-blocked commit.

Schema (`vanguard_audit` collection):
  {
    _id:             ObjectId,
    ts:              ISO8601 string,
    ts_unix:         float,
    user_id:         str,
    project:         str,                 # "owner/repo@branch"
    project_id:      str | None,
    task_id:         str | None,
    rule_triggered:  str,                 # first/top rule slug
    findings:        list[dict],          # [{file, line, severity, rule, message, source}]
    layer_summary:   str,                 # "regex: BLOCK (3) | verify-agent: pass | e2b: skipped"
    layers_blocked:  list[str],           # ["regex", "verify-agent"]
  }

Provides:
  log_blocked_commit(...)   — append-only writer
  weekly_stats(db)          — { total_blocked, top_rule, by_project, by_severity }
  recent_blocks(db, limit)  — last N rows for table view
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _now() -> tuple[float, str]:
    n = time.time()
    return n, datetime.now(timezone.utc).isoformat()


def _layer_blocked(verify_result: dict) -> list[str]:
    """Which layer(s) actually blocked the commit?"""
    out: list[str] = []
    if verify_result.get("regex", {}).get("blocked"):
        out.append("regex")
    agent = verify_result.get("agent") or {}
    if agent.get("model") and not agent.get("pass", True):
        out.append("verify-agent")
    e2b = verify_result.get("e2b") or {}
    if not e2b.get("skipped") and not e2b.get("pass", True):
        out.append("e2b")
    return out


def _top_rule(findings: list[dict]) -> str:
    """Pick the rule slug from the first CRITICAL/HIGH finding (or first
    finding if none). Used as the headline label in the dashboard."""
    if not findings:
        return "unknown"
    by_sev = sorted(
        findings,
        key=lambda f: ({"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
                       .get(f.get("severity", "LOW"), 9)),
    )
    top = by_sev[0]
    return (top.get("rule") or top.get("name") or top.get("type")
            or "unknown")[:64]


async def log_blocked_commit(
    db,
    *,
    user_id: str,
    project: str,
    verify_result: dict,
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> None:
    """Append a single row. Designed to NEVER throw — auditing failures
    must not block the user-facing commit-rejected response."""
    if db is None:
        return
    try:
        ts_unix, ts_iso = _now()
        findings = list(verify_result.get("findings", []) or [])
        doc = {
            "ts":             ts_iso,
            "ts_unix":        ts_unix,
            "user_id":        str(user_id or "unknown"),
            "project":        str(project or "")[:200],
            "project_id":     project_id,
            "task_id":        task_id,
            "rule_triggered": _top_rule(findings),
            "findings":       findings[:25],   # cap to keep rows light
            "layer_summary":  verify_result.get("summary", "")[:500],
            "layers_blocked": _layer_blocked(verify_result),
            "total_findings": len(findings),
        }
        await db.vanguard_audit.insert_one(doc)
        # Best-effort index — idempotent and cheap on first call.
        try:
            await db.vanguard_audit.create_index([("ts_unix", -1)])
            await db.vanguard_audit.create_index([("user_id", 1)])
            await db.vanguard_audit.create_index([("project", 1)])
            await db.vanguard_audit.create_index([("rule_triggered", 1)])
        except Exception:
            pass
    except Exception as e:
        logger.warning("vanguard_audit insert failed: %r", e)


async def weekly_stats(db, *, since_days: int = 7) -> dict:
    """Stats for the admin dashboard. Resilient to empty collection."""
    if db is None:
        return {"total_blocked": 0, "top_rule": None, "by_rule": [],
                "by_project": [], "by_severity": [], "by_day": []}
    since_ts = time.time() - (since_days * 86_400)
    match = {"$match": {"ts_unix": {"$gte": since_ts}}}

    total = await db.vanguard_audit.count_documents(
        {"ts_unix": {"$gte": since_ts}}
    )

    async def _agg(stage):
        try:
            return [d async for d in db.vanguard_audit.aggregate([match, *stage])]
        except Exception as e:
            logger.warning("vanguard_audit agg failed: %r", e)
            return []

    by_rule = await _agg([
        {"$group": {"_id": "$rule_triggered", "count": {"$sum": 1}}},
        {"$sort":  {"count": -1}},
        {"$limit": 10},
        {"$project": {"_id": 0, "rule": "$_id", "count": 1}},
    ])
    by_project = await _agg([
        {"$group": {"_id": "$project", "count": {"$sum": 1}}},
        {"$sort":  {"count": -1}},
        {"$limit": 10},
        {"$project": {"_id": 0, "project": "$_id", "count": 1}},
    ])
    by_severity = await _agg([
        {"$unwind": {"path": "$findings", "preserveNullAndEmptyArrays": False}},
        {"$group": {"_id": "$findings.severity", "count": {"$sum": 1}}},
        {"$sort":  {"count": -1}},
        {"$project": {"_id": 0, "severity": "$_id", "count": 1}},
    ])
    # Day-bucketed series for a sparkline (UTC dates)
    by_day = await _agg([
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d",
                                       "date": {"$toDate": {"$multiply": ["$ts_unix", 1000]}}}},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
        {"$project": {"_id": 0, "day": "$_id", "count": 1}},
    ])
    top_rule = by_rule[0]["rule"] if by_rule else None
    return {
        "total_blocked": total,
        "top_rule":      top_rule,
        "by_rule":       by_rule,
        "by_project":    by_project,
        "by_severity":   by_severity,
        "by_day":        by_day,
        "window_days":   since_days,
    }


async def recent_blocks(db, *, limit: int = 25) -> list[dict]:
    """Most recent N blocked-commit rows for the table view."""
    if db is None:
        return []
    rows: list[dict] = []
    cur = db.vanguard_audit.find(
        {},
        {
            "_id": 0, "ts": 1, "user_id": 1, "project": 1,
            "rule_triggered": 1, "total_findings": 1,
            "layers_blocked": 1, "task_id": 1,
        },
    ).sort("ts_unix", -1).limit(int(limit))
    async for d in cur:
        rows.append(d)
    return rows
