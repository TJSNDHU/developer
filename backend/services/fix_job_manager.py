"""
services/fix_job_manager.py — Iter 212m-128

Hybrid (in-memory + Mongo) job manager for live fix progress.

DESIGN — why hybrid?
  • SSE clients need an in-memory `asyncio.Queue` to receive events
    with sub-millisecond latency.  Mongo is too slow for that.
  • A pod restart, multi-instance load balancer, or a silently-
    crashed background task must NOT make the job invisible to the
    user — Mongo is the durable source of truth.
  • Job state is updated in BOTH stores:
      – in-memory: full real-time queue + counters (fast path)
      – Mongo (`fix_jobs` collection): snapshot of counters,
        pending_findings, completed_results so a different pod / a
        page reload can resume or replay.

LIFECYCLE
  create_job(...)                     → status:"running"
  emit("queued", ...)                 → push to in-mem queue
  emit("fix-done", ok=True, ...)      → snapshot to Mongo
  close(ok=True)                      → status:"done"
  close(ok=False, reason="error")     → status:"failed"
  worker_crashed()                    → status:"orphaned" (set by
                                        a try/except in fix_pipeline
                                        OR by mark_running_orphaned()
                                        on boot)

RESUME / RESTART
  • routers/fix_pipeline.py exposes POST /restart/{job_id} that:
      1. Reads the persisted job from Mongo.
      2. Subtracts completed/terminally-failed finding_ids from
         all_findings → remaining.
      3. Starts a NEW worker on `remaining`, optionally re-using the
         same `job_id` (we use a new one for clean event streams).

ORPHAN DETECTION ON BOOT
  • `mark_running_orphaned()` is called from main.py lifespan; flips
    any `status:"running"` row to `status:"orphaned"` since their
    background task died with the previous pod.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import AsyncGenerator, Optional

logger = logging.getLogger("aurem-dev.fix_job_manager")


# ─── In-memory cache (fast path for SSE) ───────────────────────────
_JOBS: dict[str, dict] = {}
_MAX_JOBS = 1000
_TTL_SECONDS = 10 * 60


# ─── Mongo writeback (best-effort) ─────────────────────────────────
async def _persist(db, job_id: str, patch: dict) -> None:
    """Update or upsert a single fix_jobs row.  Best-effort: any
    Mongo error is logged at debug level and ignored — the in-mem
    queue is still the authoritative source while the job is in
    flight."""
    if db is None:
        return
    try:
        await db.fix_jobs.update_one(
            {"job_id": job_id},
            {"$set": {**patch, "updated_at": time.time()}},
            upsert=True,
        )
    except Exception as e:                                # noqa: BLE001
        logger.debug("fix_jobs persist failed job=%s err=%r", job_id, e)


def _gc() -> None:
    now = time.time()
    expired = [
        jid for jid, j in _JOBS.items()
        if j.get("closed_at") and now - j["closed_at"] > _TTL_SECONDS
    ]
    for jid in expired:
        _JOBS.pop(jid, None)
    if len(_JOBS) > _MAX_JOBS:
        closed_sorted = sorted(
            ((jid, j.get("closed_at") or 0) for jid, j in _JOBS.items()),
            key=lambda x: x[1],
        )
        for jid, _ in closed_sorted[: len(_JOBS) - _MAX_JOBS]:
            _JOBS.pop(jid, None)


# ─── Public API ────────────────────────────────────────────────────
async def create_job(*, db, user_id: str, kind: str, total: int = 1,
                     project_id: Optional[str] = None,
                     findings: Optional[list] = None) -> str:
    """Allocate a new job.  Persists the initial row to Mongo so a
    pod crash before the first emit still leaves a recoverable record.
    """
    _gc()
    job_id = f"fx_{uuid.uuid4().hex[:14]}"
    findings = findings or []
    _JOBS[job_id] = {
        "job_id":     job_id,
        "user_id":    user_id,
        "project_id": project_id,
        "kind":       kind,
        "total":      max(1, int(total or 1)),
        "completed":  0,
        "failed":     0,
        "started_at": time.time(),
        "closed_at":  None,
        "queue":      asyncio.Queue(),
        "results":    [],
        "all_findings":      list(findings),
        "completed_ids":     set(),
        "failed_terminal_ids": set(),
    }
    await _persist(db, job_id, {
        "job_id":              job_id,
        "user_id":             user_id,
        "project_id":          project_id,
        "kind":                kind,
        "status":              "running",
        "total":               max(1, int(total or 1)),
        "completed":           0,
        "failed":              0,
        "started_at":          time.time(),
        "all_findings":        list(findings),
        "completed_ids":       [],
        "failed_terminal_ids": [],
        "results":             [],
    })
    logger.info("fix_job created job=%s user=%s kind=%s total=%s",
                job_id, user_id, kind, total)
    return job_id


def emit(job_id: str, phase: str, **payload) -> None:
    """Push an event onto the in-memory queue.  Mongo persistence
    fans out via persist_event() (called from fix_pipeline for state
    transitions that matter — we don't write every "reading" to
    Mongo because that would churn the collection)."""
    j = _JOBS.get(job_id)
    if not j:
        return
    event = {"phase": phase, "ts": time.time(), **payload}
    if phase == "fix-done":
        j["completed"] += 1
        fid = payload.get("finding_id")
        if payload.get("ok"):
            if fid:
                j["completed_ids"].add(fid)
            j["results"].append({
                "finding_id": fid,
                "ok":         True,
                "commit_sha": payload.get("commit_sha"),
                "html_url":   payload.get("html_url"),
                "file":       payload.get("file"),
                "rule_id":    payload.get("rule_id"),
            })
        else:
            j["failed"] += 1
            err = payload.get("error") or ""
            # Terminal errors → can't be auto-retried by /restart.
            if err in {"github_credentials_missing", "github_unauthorized",
                       "insufficient_tokens", "insufficient_tokens_midbatch",
                       "file_too_large"}:
                if fid:
                    j["failed_terminal_ids"].add(fid)
            j["results"].append({
                "finding_id": fid,
                "ok":         False,
                "error":      err,
                "file":       payload.get("file"),
                "rule_id":    payload.get("rule_id"),
            })
    try:
        j["queue"].put_nowait(event)
    except Exception:
        logger.debug("emit queue put failed job=%s phase=%s", job_id, phase)


async def persist_event(db, job_id: str) -> None:
    """Snapshot the current in-mem counters to Mongo.  Called from
    fix_pipeline after every fix-done / batch-end / retry so the
    durable row stays close to live state."""
    j = _JOBS.get(job_id)
    if not j or db is None:
        return
    await _persist(db, job_id, {
        "completed":           j["completed"],
        "failed":              j["failed"],
        "results":             j["results"],
        "completed_ids":       sorted(j["completed_ids"]),
        "failed_terminal_ids": sorted(j["failed_terminal_ids"]),
    })


async def close(db, job_id: str, *, ok: bool = True,
                message: Optional[str] = None,
                status: Optional[str] = None) -> None:
    """Mark a job terminal.  `status` overrides the default
    (`"done"` if ok, `"failed"` otherwise) — fix_pipeline uses
    `"failed"` for top-level exceptions and `"orphaned"` for jobs
    that lost their worker."""
    j = _JOBS.get(job_id)
    if not j:
        # Still patch Mongo so the persisted row reflects the
        # terminal state — important for /restart to find it.
        await _persist(db, job_id, {
            "status":     status or ("done" if ok else "failed"),
            "closed_at":  time.time(),
            "ok":         ok,
            "message":    message or "",
        })
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
        "status":    status or ("done" if ok else "failed"),
    }
    try:
        j["queue"].put_nowait(final)
        j["queue"].put_nowait(None)
    except Exception:
        pass
    await _persist(db, job_id, {
        "status":     status or ("done" if ok else "failed"),
        "closed_at":  j["closed_at"],
        "ok":         ok,
        "completed":  j["completed"],
        "failed":     j["failed"],
        "results":    j["results"],
        "completed_ids":       sorted(j["completed_ids"]),
        "failed_terminal_ids": sorted(j["failed_terminal_ids"]),
        "message":    message or "",
    })


async def subscribe(job_id: str, *, db=None) -> AsyncGenerator[dict, None]:
    """Yield events from a job's queue until a sentinel `None`.

    Iter 212m-128 — Mongo hydration:
    If the job is no longer in memory (pod restart, multi-instance
    miss), we read the persisted row from Mongo and emit a single
    synthetic event reflecting its terminal state so the client can
    render a restart UI instead of an opaque "gone".
    """
    j = _JOBS.get(job_id)
    if not j:
        # Mongo fallback.
        if db is not None:
            try:
                doc = await db.fix_jobs.find_one(
                    {"job_id": job_id}, {"_id": 0},
                )
            except Exception:                            # noqa: BLE001
                doc = None
            if doc:
                status = doc.get("status") or "orphaned"
                yield {
                    "phase":     "hydrated",
                    "ts":        time.time(),
                    "status":    status,
                    "completed": doc.get("completed") or 0,
                    "failed":    doc.get("failed") or 0,
                    "total":     doc.get("total") or 0,
                    "results":   doc.get("results") or [],
                    "kind":      doc.get("kind"),
                    "project_id": doc.get("project_id"),
                    "started_at": doc.get("started_at"),
                    "closed_at":  doc.get("closed_at"),
                    "message":    doc.get("message")
                                  or ("Fix completed before reconnect."
                                      if status == "done"
                                      else "Worker lost — restart to resume."),
                    "can_restart": status in ("orphaned", "failed", "running"),
                }
                return
        yield {"phase": "gone", "ts": time.time(),
               "message": "Job not found (may have expired)",
               "can_restart": False}
        return
    q: asyncio.Queue = j["queue"]
    while True:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=30.0)
        except asyncio.TimeoutError:
            # Heartbeat keep-alive — also a "proof of life" signal
            # so the UI's idle-detector can stay in the green zone.
            yield {"phase": "heartbeat", "ts": time.time()}
            continue
        if ev is None:
            return
        yield ev


def get_summary(job_id: str) -> Optional[dict]:
    j = _JOBS.get(job_id)
    if not j:
        return None
    return {
        "job_id":     j["job_id"],
        "user_id":    j.get("user_id"),
        "kind":       j["kind"],
        "total":      j["total"],
        "completed":  j["completed"],
        "failed":     j["failed"],
        "started_at": j["started_at"],
        "closed_at":  j["closed_at"],
        "results":    j["results"],
    }


# ─── Boot-time orphan detection ────────────────────────────────────
async def mark_running_orphaned(db) -> int:
    """Called from main.py lifespan at startup.  Any `fix_jobs` row
    that's still flagged `status:"running"` was killed by the
    previous pod's restart and has no running asyncio task — flip
    it to `"orphaned"` so the UI can offer "Restart" instead of
    silently appearing to hang."""
    if db is None:
        return 0
    try:
        res = await db.fix_jobs.update_many(
            {"status": "running"},
            {"$set": {
                "status":     "orphaned",
                "closed_at":  time.time(),
                "message":    "Worker lost — pod restarted mid-job.",
            }},
        )
        return int(res.modified_count or 0)
    except Exception as e:                                # noqa: BLE001
        logger.warning("mark_running_orphaned failed: %r", e)
        return 0


async def list_jobs(db, user_id: str, *, limit: int = 20,
                    status: Optional[str] = None) -> list[dict]:
    """List a user's recent jobs for the "Resume" UI on dashboard
    page-load."""
    if db is None:
        return []
    q: dict = {"user_id": user_id}
    if status:
        q["status"] = status
    try:
        cur = db.fix_jobs.find(
            q,
            {"_id": 0, "all_findings": 0},
        ).sort("started_at", -1).limit(int(limit))
        rows = [doc async for doc in cur]
        return rows
    except Exception as e:                                # noqa: BLE001
        logger.warning("list_jobs failed: %r", e)
        return []


async def get_persisted(db, job_id: str,
                        user_id: str) -> Optional[dict]:
    """Read a single persisted job row, owner-checked."""
    if db is None:
        return None
    try:
        doc = await db.fix_jobs.find_one(
            {"job_id": job_id, "user_id": user_id},
            {"_id": 0},
        )
        return doc
    except Exception as e:                                # noqa: BLE001
        logger.warning("get_persisted failed: %r", e)
        return None
