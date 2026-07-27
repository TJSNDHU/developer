"""services/loop_rollback.py — Iter 329 · Deploy 3-A

Real, non-force-push, history-preserving revert of a loop's shipped
GitHub commit. Wires the same `github_api_writer.revert_commit`
workhorse used by `cto_projects._run_rollback_via_api` so the loop-
mode revert produces a **new commit** on the branch (revert of the
target SHA), not a force-push.

Why this exists: pre-Iter-329, `ShipConfirmModal`'s Rollback button
was dead-clickable for every loop-mode ship (it pointed at
`/cto/tasks/{task_id}/rollback`, but loop mode never creates a
`cto_tasks` row). The button silently did nothing. This module +
the new `POST /loop/{loop_id}/rollback` route give loop-mode a REAL
rollback path for the first time.

Public surface:
    run_rollback(db, loop_id, project, commit_sha, user_token) → None
        Background-task worker. Fire-and-forget; persists progress
        onto `loop_sessions.rollback_*` fields. Never raises.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


async def _set_fields(db, loop_id: str, **fields) -> None:
    """Merge-set rollback_* fields onto the loop_sessions doc."""
    if db is None:
        return
    try:
        await db.loop_sessions.update_one(
            {"loop_id": loop_id},
            {"$set": fields},
        )
    except Exception as e:                                  # noqa: BLE001
        logger.warning("[loop-rollback %s] persist failed: %r", loop_id, e)


async def _log_step(db, loop_id: str, step: str, status: str = "info") -> None:
    """Append a step to rollback_steps array on the loop doc.
    Also mirrors as a loop_run_log entry for cross-collection audit."""
    if db is None:
        return
    try:
        await db.loop_sessions.update_one(
            {"loop_id": loop_id},
            {"$push": {"rollback_steps": {
                "step":   step,
                "status": status,
                "ts":     time.time(),
            }}},
        )
        # Iter 329 · new loop_run_log kind — captured in SYSTEM_INVENTORY
        # via the auto-append on next boot.
        await db.loop_run_log.insert_one({
            "loop_id":    loop_id,
            "phase":      "rollback",
            "kind":       "loop_rollback_step",
            "step":       step,
            "status":     status,
            "created_at": time.time(),
        })
    except Exception as e:                                  # noqa: BLE001
        logger.debug("[loop-rollback %s] log_step failed: %r", loop_id, e)


async def run_rollback(
    db,
    loop_id:    str,
    project:    dict,
    commit_sha: str,
    user_token: str,
    author_name:  Optional[str] = None,
    author_email: Optional[str] = None,
) -> None:
    """Perform the real GitHub revert. Runs as a BackgroundTask kicked
    from the router. NEVER raises — persists a `rollback_status=failed`
    row on any exception."""
    from services.github_api_writer import revert_commit as gh_api_revert

    owner  = project.get("github_owner") or project.get("owner")
    repo   = project.get("github_repo")  or project.get("repo")
    branch = project.get("branch", "main")

    def _scrub(s: str) -> str:
        # Never let PAT leak into stored errors.
        return (s or "").replace(user_token or "", "***PAT***") \
            if user_token else (s or "")

    async def _prog(step: str, status: str = "info"):
        await _log_step(db, loop_id, step, status)

    await _set_fields(
        db, loop_id,
        rollback_status="running",
        rollback_started_at=time.time(),
        rollback_commit_sha=commit_sha,
    )

    try:
        await _prog("kicking off revert via github api", "info")
        # Resolve real dev identity if not passed in.
        if not author_name or not author_email:
            try:
                from services.git_identity import resolve_git_identity
                author_name, author_email = await resolve_git_identity(
                    db, project.get("user_id") or "",
                )
            except Exception as e:                          # noqa: BLE001
                logger.debug(
                    "[loop-rollback %s] identity resolve failed: %r",
                    loop_id, e,
                )
                author_name = author_name or "AUREM CTO"
                author_email = author_email or "cto@aurem.dev"

        result = await gh_api_revert(
            owner=owner, repo=repo, branch=branch, token=user_token,
            commit_sha=commit_sha, progress=_prog,
            author_name=author_name, author_email=author_email,
        )
        rb_sha = result.get("sha") or ""
        rb_html_url = result.get("html_url") or (
            f"https://github.com/{owner}/{repo}/commit/{rb_sha}"
            if rb_sha else None
        )
        await _set_fields(
            db, loop_id,
            rollback_status="done",
            rollback_sha=rb_sha,
            rollback_html_url=rb_html_url,
            rollback_completed_at=time.time(),
        )
        await _prog(f"reverted → {(rb_sha or '')[:7]}", "success")
        logger.info(
            "[loop-rollback %s] SUCCESS · reverted %s → %s",
            loop_id, commit_sha, rb_sha,
        )
    except Exception as e:                                  # noqa: BLE001
        safe = _scrub(str(e))
        logger.exception("[loop-rollback %s] failed", loop_id)
        await _prog(f"❌ {safe}", "error")
        await _set_fields(
            db, loop_id,
            rollback_status="failed",
            rollback_error=safe,
            rollback_completed_at=time.time(),
        )
