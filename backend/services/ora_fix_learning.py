"""
services/ora_fix_learning.py — Iter 212m-129

Phase-1 learning foundation for the scan + fix pipelines.

PURPOSE
  The chat-side learning loop (`ora_learning.py`) has been silently
  capturing low-confidence AUREM replies into `ora_learning_logs`
  for months.  The scan + fix pipelines, by contrast, have been
  generating thousands of useful data-points (which rules trigger,
  which fixes succeed, which patches the validator rejected, which
  errors retry-vs-terminate) and dropping them on the floor.

  This module is the FOUNDATION for fixing that.  Phase-1 scope is
  intentionally tight: log everything to two new Mongo collections
  so the data is there for a later vector-DB / recall layer
  (Phase-2 backlog).  NO embeddings, NO LLM round-trips, NO recall
  in the prompt yet.

COLLECTIONS

  ora_fix_learning   — one row per fix attempt.
    {
      learning_id   : "fl_..." (uuid)
      user_id, project_id
      rule_id, file, severity, line, title
      category       : "vanguard"|"security"|"code_quality"|...
      scanner        : "vanguard"|"bug_hunt"|"trufflehog"|...
      outcome        : "success"|"failure"
      error_code     : str|None   (only when outcome=="failure")
      retryable      : bool        (False for _TERMINAL_ERROR_CODES)
      attempts       : int         (how many retries before terminal)
      commit_sha, html_url, pr_url, branch : real GitHub coords (success)
      verified       : bool        (only when outcome=="success")
      tokens_charged : int
      duration_ms    : int
      created_at     : float       (epoch seconds)
    }
    Indexed by (user_id, rule_id, created_at) and (rule_id,
    outcome, created_at) so later analytics ("show me rules that
    fail more than they succeed") run on indexed scans.

  ora_scan_learning  — one row per scan run.
    {
      scan_id       : "sl_..." (uuid)
      user_id, project_id
      scanner       : "vanguard"|"codebase_health"|"bug_hunt"
      categories    : ["security", "performance", ...]
      files_scanned : int
      duration_ms   : int
      counts        : {"critical": 2, "high": 5, "medium": 11, ...}
      rule_counts   : {"eval_usage": 3, "sql_injection": 1, ...}
      score         : 0-100  (codebase_health only)
      created_at    : float
    }

PUBLIC API
  await record_fix_outcome(db, *, user_id, project_id, finding,
                           result, attempts=1, duration_ms=None,
                           tokens_charged=0, scanner=None)
  await record_scan_run(db, *, user_id, project_id, scanner,
                        categories, files_scanned, counts,
                        rule_counts, duration_ms, score=None)
  await get_rule_stats(db, *, user_id=None, rule_id=None,
                       since=None, limit=20)
      → analytics helper for the admin / founder dashboards.

DESIGN PRINCIPLES
  • Never raise.  Logging failures must never break a real fix or
    scan — that's the EXACT opposite of "trust" we promised the user.
  • Never block.  All writes are fire-and-forget from the caller's
    perspective (the function awaits Mongo but the caller can do it
    in a `try/except` of its own — most callers already do).
  • No PII.  We do NOT capture before/after file contents in Phase-1
    (privacy-by-default).  Phase-2 (recall layer) will add an
    opt-in `snippet_before`/`snippet_after` capture behind a per-
    project setting.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger("aurem-dev.ora_fix_learning")


# Match the terminal-error set used in routers/fix_pipeline.py so the
# retryable flag stays in sync.  (Imported lazily to avoid circular.)
_TERMINAL_ERROR_CODES: frozenset[str] = frozenset({
    "github_credentials_missing",
    "github_unauthorized",
    "insufficient_tokens",
    "insufficient_tokens_midbatch",
    "file_too_large",
    "project_not_found_or_not_yours",
    "missing_required_args",
})


def _finding_category(finding: dict) -> tuple[str, str]:
    """Derive (category, scanner) from a finding dict.  Both default
    to "unknown" so the rows still aggregate cleanly.

    Vanguard scanner emits findings WITHOUT a `category` field (the
    rule_id IS the category for them — sql_injection, secret_leak,
    etc.).  So we also look at rule_id when the explicit category
    fields are missing.
    """
    _VANGUARD_RULES = {
        "secret_leak", "sql_injection", "nosql_injection",
        "ssti", "lpdos", "redos", "chain", "eval_usage",
        "command_injection", "xxe", "path_traversal",
        "weak_crypto", "open_redirect", "deserialization",
    }
    cat = (
        finding.get("category")
        or finding.get("scanner")
        or finding.get("source")
        or ""
    ).lower().strip()
    scanner = (
        finding.get("scanner") or finding.get("source") or cat or ""
    ).lower().strip()
    rule_id = (finding.get("rule_id") or finding.get("rule") or "").lower()
    # Map Vanguard vuln classes to a single bucket so analytics
    # ("how often does sql_injection succeed?") don't fragment across
    # near-duplicate labels.
    if cat in _VANGUARD_RULES or rule_id in _VANGUARD_RULES:
        cat = "vanguard"
        if not scanner or scanner in _VANGUARD_RULES:
            scanner = "vanguard"
    return (cat or "unknown", scanner or "unknown")


async def record_fix_outcome(
    db,
    *,
    user_id: str,
    project_id: str,
    finding: dict,
    result: dict,
    attempts: int = 1,
    duration_ms: Optional[int] = None,
    tokens_charged: int = 0,
    scanner: Optional[str] = None,
) -> None:
    """Persist one fix attempt row.  Best-effort: Mongo failures are
    logged at WARNING but NEVER propagate."""
    if db is None:
        return
    try:
        ok = bool(result.get("ok"))
        err = (result.get("error") or "") if not ok else None
        retryable = (err not in _TERMINAL_ERROR_CODES) if err else False
        category, scanner_detected = _finding_category(finding)
        row: dict[str, Any] = {
            "learning_id":   f"fl_{uuid.uuid4().hex[:12]}",
            "user_id":       user_id,
            "project_id":    project_id,
            "rule_id":       (finding.get("rule_id")
                              or finding.get("rule")
                              or "unknown"),
            "file":          finding.get("file") or finding.get("path") or "",
            "line":          finding.get("line"),
            "severity":      (finding.get("severity") or "").lower() or None,
            "title":         finding.get("title") or finding.get("message") or "",
            "category":      category,
            "scanner":       (scanner or scanner_detected),
            "outcome":       "success" if ok else "failure",
            "error_code":    err,
            "retryable":     retryable,
            "attempts":      int(attempts or 1),
            "commit_sha":    result.get("full_sha") or result.get("commit_sha"),
            "html_url":      result.get("html_url"),
            "pr_url":        result.get("pr_url"),
            "branch":        result.get("branch"),
            "verified":      bool(result.get("verified")) if ok else None,
            "tokens_charged": int(tokens_charged or 0),
            "duration_ms":   int(duration_ms) if duration_ms is not None else None,
            "created_at":    time.time(),
        }
        await db.ora_fix_learning.insert_one(row)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("record_fix_outcome dropped sample: %r", e)


async def record_scan_run(
    db,
    *,
    user_id: str,
    project_id: Optional[str],
    scanner: str,
    categories: list[str],
    files_scanned: int,
    counts: dict[str, int],
    rule_counts: dict[str, int],
    duration_ms: Optional[int] = None,
    score: Optional[int] = None,
) -> None:
    """Persist one scan-run row."""
    if db is None:
        return
    try:
        row = {
            "scan_id":       f"sl_{uuid.uuid4().hex[:12]}",
            "user_id":       user_id,
            "project_id":    project_id,
            "scanner":       (scanner or "unknown").lower(),
            "categories":    list(categories or []),
            "files_scanned": int(files_scanned or 0),
            "counts":        {k: int(v or 0) for k, v in (counts or {}).items()},
            "rule_counts":   {k: int(v or 0) for k, v in (rule_counts or {}).items()},
            "duration_ms":   int(duration_ms) if duration_ms is not None else None,
            "score":         int(score) if score is not None else None,
            "created_at":    time.time(),
        }
        await db.ora_scan_learning.insert_one(row)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("record_scan_run dropped sample: %r", e)


async def get_rule_stats(
    db,
    *,
    user_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    since: Optional[float] = None,
    limit: int = 20,
) -> list[dict]:
    """Aggregate fix outcomes by rule_id.  Returns top-N rules by
    attempt count with their success/failure breakdown — what every
    founder dashboard's "most-fixed rules" widget needs.

    Filters:
      • user_id : restrict to a single user (default = all).
      • rule_id : restrict to one rule.
      • since   : epoch seconds floor (default = no floor).
    """
    if db is None:
        return []
    try:
        match: dict[str, Any] = {}
        if user_id:
            match["user_id"] = user_id
        if rule_id:
            match["rule_id"] = rule_id
        if since:
            match["created_at"] = {"$gte": float(since)}
        pipeline: list[dict] = []
        if match:
            pipeline.append({"$match": match})
        pipeline += [
            {"$group": {
                "_id": "$rule_id",
                "total":    {"$sum": 1},
                "success":  {"$sum": {"$cond": [{"$eq": ["$outcome", "success"]}, 1, 0]}},
                "failure":  {"$sum": {"$cond": [{"$eq": ["$outcome", "failure"]}, 1, 0]}},
                "attempts": {"$sum": "$attempts"},
                "last_at":  {"$max": "$created_at"},
            }},
            {"$sort":  {"total": -1, "last_at": -1}},
            {"$limit": int(limit)},
        ]
        cur = db.ora_fix_learning.aggregate(pipeline)
        out = []
        async for doc in cur:
            t = doc.get("total") or 0
            s = doc.get("success") or 0
            out.append({
                "rule_id":   doc["_id"],
                "total":     t,
                "success":   s,
                "failure":   doc.get("failure") or 0,
                "attempts":  doc.get("attempts") or 0,
                "success_rate": round(s / t, 3) if t else None,
                "last_at":   doc.get("last_at"),
            })
        return out
    except Exception as e:                                    # noqa: BLE001
        logger.warning("get_rule_stats failed: %r", e)
        return []


async def ensure_indexes(db) -> None:
    """Idempotent index creation — called from main.py lifespan."""
    if db is None:
        return
    try:
        await db.ora_fix_learning.create_index(
            [("user_id", 1), ("rule_id", 1), ("created_at", -1)],
            name="ix_ofl_user_rule_ts",
        )
        await db.ora_fix_learning.create_index(
            [("rule_id", 1), ("outcome", 1), ("created_at", -1)],
            name="ix_ofl_rule_outcome_ts",
        )
        await db.ora_fix_learning.create_index(
            [("project_id", 1), ("created_at", -1)],
            name="ix_ofl_project_ts",
        )
        await db.ora_scan_learning.create_index(
            [("user_id", 1), ("scanner", 1), ("created_at", -1)],
            name="ix_osl_user_scanner_ts",
        )
        await db.ora_scan_learning.create_index(
            [("project_id", 1), ("created_at", -1)],
            name="ix_osl_project_ts",
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("ora_fix_learning ensure_indexes failed: %r", e)
