"""
services/fix_job_manager.py — Iter 212m-121

In-memory job manager for live fix progress.  Each fix (single or
bulk) gets a `job_id`; phases are pushed onto an async queue per job
and consumed by the SSE stream endpoint.

Why in-memory + ephemeral:
  • A fix job lasts seconds to a couple of minutes.  Persisting
    progress to Mongo would add latency for zero user value — the
    durable record (commit_sha, html_url, finding_id) is already
    stored in `finding_fixes` by `apply_finding_fix`.
  • One backend pod processes its own jobs.  No multi-pod fanout
    needed for v1.
  • If a pod restarts mid-job, the SSE stream closes; the client
    retries `GET /fix-stream/{job_id}` and gets `phase: gone`,
    surfacing a "Fix may have completed — check GitHub" notice.

Concurrency model:
  • Single-finding fixes can run in parallel (each its own job_id).
  • A BULK fix runs all findings sequentially inside ONE job_id to
    avoid Git conflicts on the same branch — events stream in order.

Public surface (used by routers/codebase_health.py):
  • create_job(user_id, kind, total) -> job_id
  • emit(job_id, phase, **payload)
  • subscribe(job_id) -> async generator of dict events
  • close(job_id) — flushes a terminal sentinel
  • get_summary(job_id) -> dict (for the dashboard list)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import AsyncGenerator, Optional

logger = logging.getLogger("aurem-dev.fix_job_manager")


# Per-job event stream + summary block.
_JOBS: dict[str, dict] = {}
# Hard cap so a runaway pile of jobs can't OOM us in dev.
_MAX_JOBS = 1000
# How long after `close()` we keep the summary in memory so a slow
# client can still hit `GET /fix-stream/{job_id}` and pick up the
# terminal events.  10 min covers UI lag + tab-switch.
_TTL_SECONDS = 10 * 60


def _gc() -> None:
    """Drop expired jobs.  Cheap O(n) sweep called on every create."""
    now = time.time()
    expired = [
        jid for jid, j in _JOBS.items()
        if j.get("closed_at") and now - j["closed_at"] > _TTL_SECONDS
    ]
    for jid in expired:
        _JOBS.pop(jid, None)
    # If we're still over the cap (lots of in-flight), drop the
    # oldest CLOSED ones first.
    if len(_JOBS) > _MAX_JOBS:
        closed_sorted = sorted(
            ((jid, j.get("closed_at") or 0) for jid, j in _JOBS.items()),
            key=lambda x: x[1],
        )
        for jid, _ in closed_sorted[: len(_JOBS) - _MAX_JOBS]:
            _JOBS.pop(jid, None)


def create_job(user_id: str, kind: str, total: int = 1) -> str:
    """Allocate a new job. `kind` is "single" or "bulk". `total` is
    the expected number of fixes for bulk preview / progress %.
    Returns the new job_id."""
    _gc()
    job_id = f"fx_{uuid.uuid4().hex[:14]}"
    _JOBS[job_id] = {
        "job_id":     job_id,
        "user_id":    user_id,
        "kind":       kind,
        "total":      max(1, int(total or 1)),
        "completed":  0,
        "failed":     0,
        "started_at": time.time(),
        "closed_at":  None,
        "queue":      asyncio.Queue(),
        "results":    [],         # list of {finding_id, ok, commit_sha, html_url, error}
    }
    logger.info("fix_job created job=%s user=%s kind=%s total=%s",
                job_id, user_id, kind, total)
    return job_id


def emit(job_id: str, phase: str, **payload) -> None:
    """Push an event onto the job queue.  No-ops silently when the
    job has been GC'd so a late callback never crashes the worker."""
    j = _JOBS.get(job_id)
    if not j:
        return
    event = {"phase": phase, "ts": time.time(), **payload}
    # Update terminal counters so the summary stays correct even if
    # the client never subscribed.
    if phase == "fix-done":
        j["completed"] += 1
        if payload.get("ok"):
            j["results"].append({
                "finding_id": payload.get("finding_id"),
                "ok":         True,
                "commit_sha": payload.get("commit_sha"),
                "html_url":   payload.get("html_url"),
                "file":       payload.get("file"),
                "rule_id":    payload.get("rule_id"),
            })
        else:
            j["failed"] += 1
            j["results"].append({
                "finding_id": payload.get("finding_id"),
                "ok":         False,
                "error":      payload.get("error"),
                "file":       payload.get("file"),
                "rule_id":    payload.get("rule_id"),
            })
    try:
        j["queue"].put_nowait(event)
    except Exception:
        # Queue is unbounded — only path here is a closed loop.
        logger.debug("emit queue put failed job=%s phase=%s", job_id, phase)


def close(job_id: str, ok: bool = True, message: Optional[str] = None) -> None:
    """Mark a job terminal.  Sends a final `done` event + a sentinel
    `None` so subscribers can exit their async generators."""
    j = _JOBS.get(job_id)
    if not j:
        return
    j["closed_at"] = time.time()
    final = {
        "phase":     "done",
        "ts":        j["closed_at"],
        "ok":        ok,
        "completed": j["completed"],
        "failed":    j["failed"],
        "total":     j["total"],
        "results":   j["results"],
        "message":   message or ("Fix complete" if ok else "Fix finished with errors"),
    }
    try:
        j["queue"].put_nowait(final)
        j["queue"].put_nowait(None)
    except Exception:
        pass


async def subscribe(job_id: str) -> AsyncGenerator[dict, None]:
    """Yield events from a job's queue until a sentinel `None`. The
    queue is drained — events are consumed once.  This is acceptable
    because the contract is one client per job_id (a single fix
    drawer).  If multi-client tailing is ever needed, swap the queue
    for a pubsub fanout."""
    j = _JOBS.get(job_id)
    if not j:
        yield {"phase": "gone", "ts": time.time(),
               "message": "Job not found — may have completed and expired."}
        return
    # Replay any phase=done already on the queue so a client that
    # connects AFTER completion still sees the terminal summary.
    q: asyncio.Queue = j["queue"]
    while True:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=120.0)
        except asyncio.TimeoutError:
            # Heartbeat keep-alive so proxies don't kill idle SSE.
            yield {"phase": "heartbeat", "ts": time.time()}
            continue
        if ev is None:
            return
        yield ev


def get_summary(job_id: str) -> Optional[dict]:
    """Cheap polling fallback when SSE is unavailable.  Returns the
    current counters + accumulated per-finding results."""
    j = _JOBS.get(job_id)
    if not j:
        return None
    return {
        "job_id":     j["job_id"],
        "kind":       j["kind"],
        "total":      j["total"],
        "completed":  j["completed"],
        "failed":     j["failed"],
        "started_at": j["started_at"],
        "closed_at":  j["closed_at"],
        "results":    j["results"],
    }
