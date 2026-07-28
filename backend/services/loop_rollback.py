"""services/loop_rollback.py — Iter 329 · Deploy 3-A · Iter 330 · SSE

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

Iter 330 · SSE Integration
--------------------------
Rollback progress now emits onto the same SSE plumbing ship events
use — via `sse_replay_buffer.record()` + `loop_sessions.last_event`
Mongo write. Consumers (frontend `OperationHistory`) reopen the
existing `GET /loop/{loop_id}/stream` after ship-terminal and the
engine=None Mongo-poll branch delivers rollback events with
`phase="rollback"`.

Architectural note (documented after cross-checking loop_engine
lifecycle): the LoopEngine has already **self-deregistered** from
`_LIVE` by the time rollback fires (loop is COMPLETED). Therefore
`eng.lookup(loop_id).queue` is NOT available for rollback — the
live-queue path is impossible. The record+last_event path IS the
only viable delivery mechanism, and it's the same fallback the
existing `/stream` endpoint already uses for cross-worker cases.

Public surface:
    run_rollback(db, loop_id, project, commit_sha, user_token) → None
        Background-task worker. Fire-and-forget; persists progress
        onto `loop_sessions.rollback_*` fields AND emits SSE events.
        Never raises.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _emit_rollback_event(
    db,
    loop_id: str,
    step: int,
    total_steps: int,
    message: str,
    data: Optional[dict] = None,
    state_str: str = "executing",
) -> None:
    """Iter 330 · rollback SSE emit.

    Uses the same wire shape as ship events (`_new_event` in
    loop_engine.py) so frontend consumers can flow both through
    the same `/loop/{id}/stream` endpoint, differentiating only
    by `phase == "rollback"`.

    Delivery mechanism (per Iter 330 architecture note above):
      1. sse_replay_buffer.record() — synchronous ring-buffer store
         with seq assignment, so reconnecting clients replay any
         missed rollback events via Last-Event-ID.
      2. loop_sessions.last_event Mongo write — so the /stream
         endpoint's engine=None Mongo-poll branch picks up
         rollback events every ~2s (cross-worker friendly).

    Never raises: rollback correctness must never depend on the
    telemetry channel being healthy. `pass`-on-exception mirrors
    the fail-open discipline the engine itself uses.
    """
    if db is None:
        return
    try:
        # Import lazily to avoid circular imports (loop_engine imports
        # things that indirectly touch this module's caller chain).
        from services.loop_engine import _new_event, LoopState
        from services import sse_replay_buffer as _sse_buf

        # Map string → LoopState enum member. Fallback keeps us fail-
        # open if the caller passes an unknown value.
        state_enum = {
            "executing": LoopState.EXECUTING,
            "completed": LoopState.COMPLETED,
            "failed":    LoopState.FAILED,
        }.get((state_str or "executing").lower(), LoopState.EXECUTING)

        ev = _new_event(
            loop_id=loop_id,
            state=state_enum,
            phase="rollback",
            step=step,
            total_steps=total_steps,
            message=message,
            data=data or {},
        )
        # (1) replay buffer — sync API returns (seq, id_str); we
        # don't need the return values here, they're used by the
        # /stream endpoint when it builds the SSE `id:` line.
        try:
            _sse_buf.record(loop_id, ev)
        except Exception as e:                                  # noqa: BLE001
            logger.debug("[loop-rollback %s] replay record failed: %r",
                         loop_id, e)

        # (2) Mongo last_event — same field the ship pipeline writes
        # via _persist_session; /stream's engine=None poll reads
        # this every ~2s and streams changes to the client.
        try:
            await db.loop_sessions.update_one(
                {"loop_id": loop_id},
                {"$set": {"last_event": ev, "updated_at": _iso()}},
            )
        except Exception as e:                                  # noqa: BLE001
            logger.debug("[loop-rollback %s] last_event write failed: %r",
                         loop_id, e)
    except Exception as e:                                      # noqa: BLE001
        # Absolute fail-open: SSE emit must NEVER break rollback.
        logger.debug("[loop-rollback %s] emit skipped: %r", loop_id, e)


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

    # Iter 330 · running step counter for SSE emits. Total is unknown
    # up front (gh_api_revert fires its own progress callbacks) so we
    # bump `total` to match `step` on every call — frontend sees an
    # honest running count instead of a fake percentage. The final
    # terminal emit carries the true final count.
    _step_ctr = {"n": 0}

    async def _prog(step: str, status: str = "info"):
        await _log_step(db, loop_id, step, status)
        # Iter 330 — mirror onto SSE. Fail-open by design.
        _step_ctr["n"] += 1
        n = _step_ctr["n"]
        await _emit_rollback_event(
            db=db, loop_id=loop_id,
            step=n, total_steps=n,
            message=step,
            data={"status": status},
            state_str="executing",
        )

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
        # Iter 330 · terminal SSE emit — carries state="completed"
        # which the frontend consumer uses to close its OperationHistory
        # live-op card and add it to the collapsed history stack. Also
        # carries commit_sha + html_url so the UI can link to the revert.
        _n = _step_ctr["n"]
        await _emit_rollback_event(
            db=db, loop_id=loop_id,
            step=_n, total_steps=_n,
            message=f"Rollback finished — reverted to {(rb_sha or '')[:7]}",
            data={
                "commit_sha": rb_sha,
                "html_url":   rb_html_url,
                "status":     "success",
            },
            state_str="completed",
        )
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
        # Iter 330 · terminal SSE emit — carries state="failed"
        # + the scrubbed error string so the frontend can display it
        # without needing a second Mongo poll.
        _n = _step_ctr["n"]
        await _emit_rollback_event(
            db=db, loop_id=loop_id,
            step=_n, total_steps=_n,
            message="Rollback failed",
            data={"error": safe, "status": "error"},
            state_str="failed",
        )
