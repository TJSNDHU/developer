"""
services/loop_outcomes.py — Iter 272 Feature 2.1

Every shipped commit gets one row here. This is the substrate every
downstream drift detector reads.

The two questions this collection answers:
  1. "Did we ship a fix for THIS file recently, and now the same file
     is being touched again?" → `repeat_touch` marker on the newer
     row, referencing the earlier commit_sha.
  2. "Was a previously-shipped commit reverted?" → `reverted: true`
     + `reverted_at` on the earlier row (marked by whoever pushes
     the revert commit through `github_api_writer.revert_commit`).

Public surface:
    record_shipped_commit(db, ...) → dict
        Called from loop_engine.confirm_ship() right after the GitHub
        push succeeds. Also runs the repeat_touch check inline (cheap
        — one indexed query).
    mark_reverted(db, *, commit_sha) → dict
        Called from github_api_writer.revert_commit() when a revert
        completes.

The 14-day and 30-day windows referenced by later drift jobs are
computed from `shipped_at`. `shipped_at` is stored as ISO UTC string
for portability with the rest of the codebase (`_now()` returns ISO
strings elsewhere).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_COLL = "loop_outcomes"
_REPEAT_WINDOW_DAYS = 14


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def record_shipped_commit(db, *,
                                 loop_id: str,
                                 task_id: Optional[str],
                                 user_id: str,
                                 project_id: Optional[str],
                                 commit_sha: str,
                                 file_paths: Iterable[str],
                                 owner: str,
                                 repo: str,
                                 branch: str) -> dict:
    """Insert one outcome row and inline-check for repeat_touch. Never
    raises. Returns the (possibly-decorated) inserted row."""
    paths = sorted({str(p or "").strip() for p in (file_paths or []) if p})
    row = {
        "commit_sha":   commit_sha,
        "loop_id":      loop_id,
        "task_id":      task_id or loop_id,
        "user_id":      user_id,
        "project_id":   project_id,
        "owner":        owner,
        "repo":         repo,
        "branch":       branch,
        "file_paths":   paths,
        "shipped_at":   _now(),
        "repeat_touch": False,
        "reverted":     False,
    }

    # ── Repeat-touch scan (indexed, cheap) ────────────────────────
    if paths and project_id:
        since = _since_iso(_REPEAT_WINDOW_DAYS)
        prior = await db[_COLL].find_one(
            {
                "project_id":   project_id,
                "shipped_at":   {"$gte": since},
                "file_paths":   {"$in": paths},
                "commit_sha":   {"$ne": commit_sha},
            },
            sort=[("shipped_at", -1)],
        )
        if prior:
            row["repeat_touch"] = True
            row["repeat_of"] = {
                "commit_sha": prior.get("commit_sha"),
                "shipped_at": prior.get("shipped_at"),
                "overlap":    sorted(
                    set(paths) & set(prior.get("file_paths") or [])
                )[:20],
            }
            logger.info(
                "[loop %s] outcome: REPEAT_TOUCH against %s (overlap=%d)",
                loop_id, prior.get("commit_sha"),
                len(row["repeat_of"]["overlap"]),
            )

    try:
        await db[_COLL].insert_one(dict(row))
    except Exception as e:                                    # noqa: BLE001
        # Race on unique index → we already recorded this commit_sha.
        logger.warning("[loop %s] outcome insert failed (%r) — "
                       "returning row unpersisted", loop_id, e)
    return row


async def mark_reverted(db, *,
                         commit_sha: str,
                         reverted_by: Optional[str] = None) -> dict:
    """Called when someone pushes a revert of `commit_sha`. Idempotent.
    Returns {marked: bool, commit_sha, prior}. Never raises."""
    if not commit_sha:
        return {"marked": False, "commit_sha": commit_sha,
                "reason": "empty_sha"}
    now = _now()
    try:
        prior = await db[_COLL].find_one_and_update(
            {"commit_sha": commit_sha, "reverted": {"$ne": True}},
            {"$set": {"reverted":    True,
                       "reverted_at": now,
                       "reverted_by": reverted_by}},
        )
        return {
            "marked":     bool(prior),
            "commit_sha": commit_sha,
            "prior":      bool(prior),
        }
    except Exception as e:                                    # noqa: BLE001
        logger.warning("mark_reverted failed for %s: %r", commit_sha, e)
        return {"marked": False, "commit_sha": commit_sha,
                "reason": f"{type(e).__name__}"}


async def ensure_indexes(db) -> None:
    await db[_COLL].create_index("commit_sha", unique=True)
    await db[_COLL].create_index("project_id")
    await db[_COLL].create_index("user_id")
    await db[_COLL].create_index("shipped_at")
    await db[_COLL].create_index([("project_id", 1),
                                   ("shipped_at", -1)])
    await db[_COLL].create_index("file_paths")
    await db[_COLL].create_index("repeat_touch")
    await db[_COLL].create_index("reverted")
