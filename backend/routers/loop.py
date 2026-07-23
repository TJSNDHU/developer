"""
routers/loop.py — Iter 212m-60 (Loop Mode Phase B)

Six endpoints over the LoopEngine state machine.  All paths sit
under the /api/aurem-dev prefix from main.py:

    POST /loop/start                  → kick off a new loop (returns
                                        loop_id, runs plan-phase to
                                        completion, then pauses on
                                        AWAITING_CONFIRMATION).
    POST /loop/{loop_id}/confirm      → user approves (or rejects) the
                                        plan; engine begins
                                        EXECUTE → VERIFY → SCAN → SHIP.
    POST /loop/{loop_id}/pause-response → resume an engine paused for
                                          user input (retry/skip/abort).
    GET  /loop/{loop_id}/status       → current Mongo snapshot.
    GET  /loop/{loop_id}/stream       → SSE feed of every event since
                                        connection time, plus all
                                        future ones until terminal.
    POST /loop/{loop_id}/cancel       → user cancels.
"""
from __future__ import annotations

import asyncio
import json
import time
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db
from services import loop_engine as eng

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/loop", tags=["Loop Mode"])


# ─── Request models ───────────────────────────────────────────────────

class StartBody(BaseModel):
    project_id:   Optional[str] = None
    user_message: str            = Field(..., min_length=1, max_length=8000)


class ConfirmBody(BaseModel):
    approved: bool
    feedback: Optional[str] = Field(None, max_length=2000)


class PauseResponseBody(BaseModel):
    action:   str                = Field(..., pattern="^(retry|skip|abort)$")
    feedback: Optional[str]      = Field(None, max_length=2000)


class SubmitFilesBody(BaseModel):
    files: list[dict] = Field(..., max_length=200)


# ─── Endpoints ────────────────────────────────────────────────────────

@router.post("/start")
async def start_loop(body: StartBody,
                     authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    # Iter 212m-130 — Loop Mode is temporarily founder-only.  We've
    # had real reports of executions getting stuck in the
    # plan-confirm loop / verify retry storms and the founder needs
    # space to fix the engine before exposing it to paying users
    # again.  Non-founders see a friendly "Coming Soon" 403 — the
    # frontend toggle is also locked client-side so this should
    # only ever fire for someone hand-rolling a curl request.
    is_founder = bool(
        user.get("is_admin") or user.get("is_unlimited")
        or (user.get("tier") == "founder")
    )
    if not is_founder:
        raise HTTPException(403, {
            "error":   "loop_mode_locked",
            "message": ("Loop Mode is coming soon — we're polishing the "
                       "Plan → Execute → Verify → Scan → Ship pipeline. "
                       "It will unlock for all developers shortly."),
            "coming_soon": True,
        })
    # Iter 212m-115 safety #4 — Circuit breaker: refuse new starts if
    # this {project_id, user_id} has hit 3+ failures in the last 15 min.
    # Founders bypass (so we never block our own debugging).
    from services.loop_safety import (
        is_loop_circuit_open, acquire_loop_lock,
    )
    proj_key = body.project_id or "_no_project"
    if not is_founder:
        circuit_open, fail_count, retry_after = await is_loop_circuit_open(
            db, proj_key, user["user_id"],
        )
        if circuit_open:
            raise HTTPException(429, {
                "error":             "loop_circuit_open",
                "fail_count":        fail_count,
                "retry_after_seconds": retry_after,
                "message": (
                    f"Loop disabled for this project — {fail_count} failed "
                    f"runs in the last 15 minutes. Try again in "
                    f"{(retry_after or 0) // 60} min "
                    f"{(retry_after or 0) % 60} s."
                ),
            })

    loop_id = eng.new_loop_id()
    # Iter 212m-115 safety #2 — Concurrent-loop lock. Refuses a 2nd
    # parallel loop on the same project for the same user.
    locked, existing = await acquire_loop_lock(
        db, proj_key, user["user_id"], loop_id,
    )
    if not locked:
        raise HTTPException(409, {
            "error":            "loop_already_running",
            "existing_loop_id": (existing or {}).get("loop_id"),
            "message":          "Another loop is already running for this "
                                "project. Wait for it to finish or cancel it.",
        })

    # Iter 212m-169/170 — Build ORAContext ONCE at loop start.
    from services.ora_context import build_ora_context
    _bin_ctx_loop = await build_ora_context(
        user_id=user["user_id"],
        project_id=body.project_id,
        db=db,
        is_founder=is_founder,
    )

    engine = eng.LoopEngine(
        db=db, loop_id=loop_id,
        user_id=user["user_id"],
        project_id=body.project_id,
        user_message=body.user_message,
        bin_ctx=_bin_ctx_loop,
    )
    eng.register(engine)
    async for _ev in engine.start():
        pass
    return {
        "loop_id":      loop_id,
        "state":        engine.state.value,
        "phase":        engine.phase,
        "plan":         engine.context.get("plan"),
        "requires_user_action": engine.state.value == "awaiting_confirmation",
    }


@router.get("/active")
async def get_active_loop(
    project_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 212m-115 safety #3 — Resume on browser refresh. Returns
    the user's most recent non-terminal loop (paused at ship, plan
    awaiting confirm, etc.) so the frontend can re-hydrate the UI
    without losing the manual Ship gate.

    Scoped to {user_id, project_id} so no cross-user leak."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True, "active": None}
    q = {
        "user_id":  user["user_id"],
        "state":    {"$in": [
            "awaiting_confirmation", "executing", "verifying",
            "scanning", "shipping", "paused_for_user", "self_healing",
        ]},
    }
    if project_id:
        q["project_id"] = project_id
    doc = await db.loop_sessions.find_one(q, sort=[("updated_at", -1)])
    if not doc:
        return {"ok": True, "active": None}
    # Strip Mongo _id, GitHub PAT in ship_pending (security).
    doc.pop("_id", None)
    ctx = doc.get("context") or {}
    ship_pending = (ctx.get("ship_pending") or {}).copy()
    if ship_pending:
        ship_pending.pop("token", None)              # never leak token
    return {
        "ok":     True,
        "active": {
            "loop_id":    doc.get("loop_id"),
            "state":      doc.get("state"),
            "phase":      doc.get("phase"),
            "project_id": doc.get("project_id"),
            "plan":       ctx.get("plan"),
            "ship_pending": ship_pending if ship_pending else None,
            "files_changed": ctx.get("files_changed") or [],
            "updated_at": doc.get("updated_at"),
        },
    }


@router.post("/{loop_id}/confirm")
async def confirm_loop(loop_id: str, body: ConfirmBody,
                       authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    # Iter 212m-144 — cross-worker rehydration. With multiple uvicorn
    # workers in PROD, the start() request may have created the engine
    # on worker A while confirm() lands on worker B — `lookup()` would
    # return None even though Mongo has the persisted session. Try
    # rehydration before 404'ing.
    engine = await eng.lookup_or_rehydrate(get_db(), loop_id)
    if engine is None:
        raise HTTPException(404, "Loop not found or already finished")
    if engine.user_id != user["user_id"]:
        raise HTTPException(403, "Not your loop")
    await engine.confirm(body.approved, body.feedback or "")
    return {
        "loop_id": loop_id,
        "state":   engine.state.value,
        "phase":   engine.phase,
    }


@router.post("/{loop_id}/confirm-ship")
async def confirm_ship_endpoint(loop_id: str, body: ConfirmBody,
                                authorization: Optional[str] = Header(None)) -> dict:
    """Iter 212m-111 — Manual Ship gate. The engine pauses at
    PAUSED_FOR_USER/phase=ship with data.kind='awaiting_ship' once
    Execute/Verify/Scan are clean. The frontend then renders the
    "Ship to GitHub" button; clicking it POSTs here with
    approved=true and the engine runs the actual GitHub commit.
    `approved=false` cancels the ship (loop → ABORTED, nothing
    pushed). Founder spec: NO auto-ship — always manual."""
    user = await current_dev(authorization)
    # Iter 212m-144 — cross-worker rehydration (same reason as confirm).
    engine = await eng.lookup_or_rehydrate(get_db(), loop_id)
    if engine is None:
        raise HTTPException(404, "Loop not found or already finished")
    if engine.user_id != user["user_id"]:
        raise HTTPException(403, "Not your loop")
    # Iter 212m-177 — P0-1 idempotency: if this loop already committed,
    # return the existing commit instead of 409/no-op (double-click or
    # split-brain second worker).
    _doc = await get_db().loop_sessions.find_one(
        {"loop_id": loop_id}, {"_id": 0, "context.commit": 1, "state": 1})
    _existing = ((_doc or {}).get("context") or {}).get("commit") or {}
    if _existing.get("sha"):
        return {
            "loop_id": loop_id, "approved": True, "state": "completed",
            "already_shipped": True, "commit": _existing,
        }
    # Iter 212m-176 — validate state HERE. confirm_ship() raises
    # ValueError inside the background task where it is silently
    # swallowed (PROD symptom: 200 approved=true but no commit).
    if engine.state != eng.LoopState.PAUSED_FOR_USER or engine.phase != "ship":
        raise HTTPException(
            409,
            f"Loop is not awaiting ship confirmation "
            f"(state={engine.state.value}, phase={engine.phase}).",
        )
    try:
        # Run as a background task so the HTTP response doesn't block
        # on the GitHub commit (which can take 3-10s). The SSE stream
        # already delivers the COMPLETED / FAILED event to the UI.
        import asyncio as _asyncio
        _asyncio.create_task(engine.confirm_ship(body.approved))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {
        "loop_id":  loop_id,
        "approved": body.approved,
        "state":    engine.state.value,
        "phase":    engine.phase,
    }


@router.post("/{loop_id}/pause-response")
async def pause_response(loop_id: str, body: PauseResponseBody,
                         authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    # Iter 212m-144 — cross-worker rehydration.
    engine = await eng.lookup_or_rehydrate(get_db(), loop_id)
    if engine is None:
        raise HTTPException(404, "Loop not found")
    if engine.user_id != user["user_id"]:
        raise HTTPException(403, "Not your loop")
    if body.action == "abort":
        await engine.cancel()
    elif body.action in ("retry", "skip"):
        # Phase C implements true retry/skip semantics; Phase B simply
        # resumes the pipeline from the next phase.
        # Iter 212m-176 — confirm() guards on AWAITING_CONFIRMATION and
        # raised ValueError when we pre-set EXECUTING (every retry/skip
        # 499'd in PROD). Set the state confirm() expects instead.
        engine.state = eng.LoopState.AWAITING_CONFIRMATION
        await engine.confirm(True, feedback=f"resume:{body.action}")
    return {
        "loop_id": loop_id,
        "state":   engine.state.value,
        "phase":   engine.phase,
    }


@router.post("/{loop_id}/submit-files")
async def submit_files(loop_id: str, body: SubmitFilesBody,
                       authorization: Optional[str] = Header(None)) -> dict:
    """Register files (path+content) that the engine's VERIFY phase
    should lint + self-heal.  Called by the chat orchestrator or the
    front-end after Step 2 finishes writing."""
    user = await current_dev(authorization)
    # Iter 212m-144 — cross-worker rehydration.
    engine = await eng.lookup_or_rehydrate(get_db(), loop_id)
    if engine is None:
        raise HTTPException(404, "Loop not found")
    if engine.user_id != user["user_id"]:
        raise HTTPException(403, "Not your loop")
    await engine.submit_files(body.files)
    return {
        "loop_id":   loop_id,
        "file_count": len(engine.context.get("submitted_files") or []),
    }


@router.get("/{loop_id}/status")
async def loop_status(loop_id: str,
                      authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    doc = await eng.load_session(db, loop_id)
    if not doc:
        raise HTTPException(404, "Loop not found")
    if doc.get("user_id") != user["user_id"]:
        raise HTTPException(403, "Not your loop")
    return doc


@router.get("/{loop_id}/stream")
async def loop_stream(loop_id: str,
                      authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    engine = eng.lookup(loop_id)
    if engine is not None and engine.user_id != user["user_id"]:
        raise HTTPException(403, "Not your loop")
    if engine is None:
        # Iter 212m-177 — P1-7: the loop may be running on ANOTHER
        # worker (multi-worker PROD). Don't 404 — fall back to replaying
        # `last_event` from Mongo, which _emit() persists on every event.
        _doc = await get_db().loop_sessions.find_one(
            {"loop_id": loop_id}, {"_id": 0, "user_id": 1, "state": 1})
        if not _doc:
            raise HTTPException(404, "Loop not found")
        if _doc.get("user_id") != user["user_id"]:
            raise HTTPException(403, "Not your loop")

    _TERMINAL = {"completed", "failed", "aborted"}
    # Release It! Governor pattern — hard wall-clock ceiling on the
    # SSE stream. Without this, a loop that gets stuck in a
    # non-terminal state (executing/verifying/scanning) can keep this
    # generator alive indefinitely and tie up an app worker. 20 min
    # is well above any observed real end-to-end run (self-heals
    # included). At the ceiling we emit a synthetic terminal frame
    # so the frontend disconnects cleanly rather than seeing a
    # silent EOF.
    _STREAM_MAX_S = 20 * 60

    async def gen():
        db = get_db()
        sent_sig = None
        _stream_started = time.monotonic()
        try:
            while True:
                if time.monotonic() - _stream_started > _STREAM_MAX_S:
                    _cap_min = _STREAM_MAX_S // 60
                    terminal_ev = {
                        "state":   "aborted",
                        "phase":   "?",
                        "message": (
                            f"SSE stream capped at {_cap_min} min — "
                            "no terminal state received. Reconnect "
                            "if the loop is still running."
                        ),
                        "ts": time.time(),
                    }
                    yield f"data: {json.dumps(terminal_ev)}\n\n"
                    break
                ev = None
                if engine is not None:
                    try:
                        ev = await asyncio.wait_for(engine.queue.get(), 5.0)
                    except asyncio.TimeoutError:
                        ev = None
                else:
                    await asyncio.sleep(2.0)
                if ev is not None:
                    sent_sig = (ev.get("ts"), ev.get("state"),
                                ev.get("phase"), ev.get("message"))
                    yield f"data: {json.dumps(ev)}\n\n"
                    if engine.state in {eng.LoopState.COMPLETED,
                                        eng.LoopState.FAILED,
                                        eng.LoopState.ABORTED}:
                        break
                    continue
                # No local event — sync from Mongo. Catches BOTH the
                # engine-on-another-worker case AND a stale local engine
                # whose pipeline continued elsewhere after a rehydrated
                # confirm (the mobile "ship button never appeared" bug).
                doc = await db.loop_sessions.find_one(
                    {"loop_id": loop_id},
                    {"_id": 0, "last_event": 1, "state": 1})
                if not doc:
                    break
                mev = doc.get("last_event") or {}
                sig = (mev.get("ts"), mev.get("state"),
                       mev.get("phase"), mev.get("message"))
                if mev and sig != sent_sig:
                    sent_sig = sig
                    yield f"data: {json.dumps(mev)}\n\n"
                else:
                    yield ": keepalive\n\n"
                if (doc.get("state") or "").lower() in _TERMINAL:
                    break
        finally:
            eng.deregister(loop_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{loop_id}/cancel")
async def cancel_loop(loop_id: str,
                      authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    # Iter 212m-144 — try local lookup first for liveness, then
    # rehydrate from Mongo so cancel works cross-worker.
    engine = eng.lookup(loop_id)
    if engine is None:
        engine = await eng.lookup_or_rehydrate(get_db(), loop_id)
    if engine is None:
        # Iter 212m-145 — Fallback path for loops that are neither
        # live in _LIVE nor rehydratable (state already FAILED/ABORTED
        # or unknown). Without this, the `loop_locks` collection still
        # holds a stale lock entry from the original run and the user
        # gets "loop_already_running" forever on the next /start.
        #
        # ROOT FIX: persist `state=aborted` to `loop_sessions` AND
        # release the lock + record a no-op failure in the safety
        # circuit so retries work immediately.
        db = get_db()
        if db is not None:
            doc = await eng.load_session(db, loop_id)
            if doc and doc.get("user_id") == user["user_id"]:
                # Iter 277 — write the aborted state AND write a terminal
                # audit row that the frontend's SSE fallback path picks
                # up. Previously this fallback branch only set state in
                # `loop_sessions`, leaving the SSE stream with no
                # terminal frame — the UI kept rendering the stale
                # "executing" state for minutes until the user refreshed.
                from datetime import datetime, timezone
                terminal_ts = datetime.now(timezone.utc).isoformat()
                await db.loop_sessions.update_one(
                    {"loop_id": loop_id},
                    {"$set": {"state": "aborted",
                              "phase": doc.get("phase") or "?",
                              "updated_at": terminal_ts,
                              "last_event": {
                                  "state":   "aborted",
                                  "phase":   doc.get("phase") or "?",
                                  "message": "Loop cancelled by user "
                                             "(no live engine — "
                                             "cleaned up via fallback).",
                                  "ts":      terminal_ts,
                              }}},
                )
                # Also drop an event row into `loop_events` so any
                # /stream consumer polling the log picks up the terminal
                # marker on its next tick.
                try:
                    await db.loop_events.insert_one({
                        "loop_id":  loop_id,
                        "state":    "aborted",
                        "phase":    doc.get("phase") or "?",
                        "message":  "Loop cancelled by user "
                                    "(no live engine — cleaned up via "
                                    "fallback).",
                        "step":     None,
                        "data":     {"origin": "cancel_fallback"},
                        "created_at": terminal_ts,
                    })
                except Exception as e:                        # noqa: BLE001
                    logger.debug(
                        "loop %s — fallback loop_events insert failed: %r",
                        loop_id, e,
                    )
                # Free the concurrent-loop lock so the project isn't
                # held captive by a ghost loop. Pulls owner out of the
                # persisted doc — _no_project for legacy / no-project
                # runs.
                try:
                    from services.loop_safety import release_loop_lock
                    proj_key = doc.get("project_id") or "_no_project"
                    await release_loop_lock(
                        db, proj_key, doc.get("user_id") or user["user_id"],
                        loop_id,
                    )
                except Exception as e:                          # noqa: BLE001
                    logger.debug(
                        "loop %s — fallback release_loop_lock failed: %r",
                        loop_id, e,
                    )
                return {"loop_id": loop_id, "state": "aborted",
                        "lock_released": True,
                        "terminal_event_written": True}
        raise HTTPException(404, "Loop not found")
    if engine.user_id != user["user_id"]:
        raise HTTPException(403, "Not your loop")
    await engine.cancel()
    # Iter 279 — belt-and-suspenders lock + state force-release.
    # `engine.cancel()` above ALREADY does `_persist_session(ABORTED)`
    # + `release_loop_lock()`, but the pipeline task's own finally
    # block (Parliament call unwinding via CancelledError) can race
    # and re-persist an interim state or re-acquire the lock. Doing
    # a second write here — AFTER cancel() returns — guarantees a
    # clean terminal DB state before the HTTP response returns to the
    # user, so an immediately-following /loop/start acquire_loop_lock
    # succeeds.
    try:
        db2 = get_db()
        if db2 is not None:
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).isoformat()
            await db2.loop_sessions.update_one(
                {"loop_id": loop_id},
                {"$set": {"state": "aborted",
                          "updated_at": ts,
                          "last_event": {
                              "state":   "aborted",
                              "phase":   engine.phase or "?",
                              "message": "Loop cancelled by user.",
                              "ts":      ts,
                          }}},
            )
            await db2.loop_locks.delete_many({
                "project_id": engine.project_id or "_no_project",
                "user_id":    user["user_id"],
                "loop_id":    loop_id,
            })
    except Exception as e:                                # noqa: BLE001
        logger.debug("iter279 force-clean after cancel failed: %r", e)
    return {"loop_id": loop_id, "state": engine.state.value,
            "lock_force_released": True}


# ── Iter 212m-146 — Force-release loop lock (safety hatch) ───────────
#
# A loop lock can theoretically be held even after `cancel()` /
# `/cancel` fallback / Iter 212m-145 auto-sweep all fail (e.g. Mongo
# write contention during the cancel write, multi-worker race, etc.).
# This endpoint is the founder-grade escape hatch: it deletes the
# lock for (project_id, caller's user_id) AND marks any session row
# with that loop_id as aborted. Founder-only to prevent accidental
# user abuse — but founders can use it on their own projects.
class ForceReleaseBody(BaseModel):
    project_id: str = Field(..., min_length=1, max_length=128)


@router.post("/force-release-lock")
async def force_release_lock(
    body: ForceReleaseBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Founder-gated. Forcibly delete the loop_lock entry for the
    caller's (project_id, user_id) and mark any associated session
    row as aborted. Returns the deleted lock's `loop_id` (if any) so
    the caller has audit trail."""
    user = await current_dev(authorization)
    if not (user.get("is_admin")
            or user.get("is_unlimited")
            or (user.get("tier") or "").lower() == "founder"):
        raise HTTPException(403, "founder access required")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")
    existing = await db.loop_locks.find_one(
        {"project_id": body.project_id, "user_id": user["user_id"]},
        {"_id": 0},
    )
    if not existing:
        return {"ok": True, "released_loop_id": None,
                "message": "no lock held"}
    deleted_loop_id = existing.get("loop_id")
    await db.loop_locks.delete_one(
        {"project_id": body.project_id, "user_id": user["user_id"]},
    )
    if deleted_loop_id:
        try:
            await db.loop_sessions.update_one(
                {"loop_id": deleted_loop_id},
                {"$set": {"state": "aborted"}},
            )
        except Exception as e:                                # noqa: BLE001
            logger.debug(
                "force-release: session state update failed: %r", e,
            )
    logger.info(
        "[loop_safety] founder %s force-released lock for project %s "
        "(was loop_id=%s)",
        user.get("user_id"), body.project_id, deleted_loop_id,
    )
    return {"ok": True, "released_loop_id": deleted_loop_id}
