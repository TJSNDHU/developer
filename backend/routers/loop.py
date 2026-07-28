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
import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db
from services import loop_engine as eng

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/loop", tags=["Loop Mode"])

# Release It! Governor pattern — hard wall-clock ceiling on the SSE
# generator (Iter 282). Module-scoped so the /_diagnostics endpoint
# can inspect the ACTUAL runtime value, not a hardcoded echo.
STREAM_MAX_S = 20 * 60

# All 6 loop-machinery collections that Iter 282 gave TTL indexes.
# Module-scoped for the same "real runtime inspection" reason.
_TTL_MANAGED_COLLECTIONS = (
    "loop_events",
    "loop_locks",
    "loop_failures",
    "loop_sessions",
    "loop_verification_log",
    "loop_run_log",
)


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

    # ── Iter 312 · Class 1 — Fire-and-forget plan phase ──────────────
    # Previously: `async for _ev in engine.start(): pass` synchronously
    # consumed the entire generator, blocking the HTTP response until
    # plan phase completed. For complex tasks whose Council/Parliament
    # consultation exceeded 60s, the frontend's blanket axios timeout
    # (frontend/src/lib/api.js:15) fired and rendered "Loop failed to
    # start" — but the backend session doc was already created and the
    # engine kept running. Chip + chat contradicted (chip was truth,
    # chat was lying). Repro'd on 2026-07-27 as loop_4473f240.
    #
    # Class 1 fix: schedule engine.start() as a background task and
    # return the initial response immediately. Session doc + lock are
    # already written by acquire_loop_lock() above (BEFORE this point),
    # so the "loop_already_running" 409 guarantee is preserved — a
    # concurrent second /loop/start would see the lock without racing
    # against the async task.
    #
    # Gated behind LOOP_START_ASYNC env flag (default True) for
    # one-flip rollback safety in case any downstream consumer still
    # expects the plan blob in the sync response body.
    _start_async = os.environ.get("LOOP_START_ASYNC", "true").lower() in ("1", "true", "yes", "on")
    if _start_async:
        asyncio.create_task(_drive_engine_to_completion(loop_id, engine))
        return {
            "loop_id":      loop_id,
            "state":        eng.LoopState.PLANNING.value,
            "phase":        "plan",
            "plan":         None,  # arrives via SSE stream
            "async_start":  True,
        }

    # Legacy sync path — retained behind flag flip for one-deploy rollback.
    # Extracted to a helper so the default execution path in `start_loop`
    # contains no blocking-consumer pattern (see repro test #1).
    return await _start_loop_sync_legacy(loop_id, engine)


async def _drive_engine_to_completion(loop_id: str, engine):
    """
    Background driver for the Iter 312 async fire-and-forget path.

    Consumes the engine.start() async generator to completion outside
    the HTTP request-response cycle. Exceptions are logged but never
    re-raised — the client already received its 200 response. The
    engine writes its own terminal state to loop_sessions on error
    so the chip / /loop/active poll sees the truth.
    """
    try:
        async for _ev in engine.start():
            pass
    except Exception as _e:
        logging.getLogger("aurem.loop").exception(
            "[loop %s] background driver crashed: %r", loop_id, _e,
        )


async def _start_loop_sync_legacy(loop_id: str, engine):
    """
    Legacy synchronous plan-phase consumer.

    Only reachable when `LOOP_START_ASYNC=false`. Kept as an escape
    hatch for one-deploy rollback if the async fire-and-forget path
    (Iter 312 · Class 1) causes any regression for downstream
    consumers that still expect the plan blob inline. Do NOT call
    this from any new code path.
    """
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
            # Iter 312 · Class 1 — `planning` MUST be included so that
            # the ChatPanel timeout-recovery poll (recovery of the
            # founder-reported loop_4473f240 desync) can see an
            # in-progress plan phase while the async background driver
            # is still consulting Council/Parliament. Excluding it
            # here re-opens the exact bug that Class 3 was supposed to
            # close: chip shows PLANNING, chat says failed, /loop/active
            # returns null so the frontend can't reconcile.
            "planning",
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


@router.post("/{loop_id}/approve-ship")
async def approve_ship_endpoint(loop_id: str,
                                authorization: Optional[str] = Header(None)) -> dict:
    """Iter 332 — Dedicated approve endpoint for the SHIP human-review
    gate (test files touched). Thin alias over confirm-ship with
    approved=True so the UI's "Approve & Ship" button has a stable,
    self-describing route. Moves PAUSED_FOR_USER/ship → SHIPPING."""
    return await confirm_ship_endpoint(
        loop_id, ConfirmBody(approved=True), authorization)


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
    elif (body.action == "skip"
          and engine.state == eng.LoopState.PAUSED_FOR_USER
          and engine.phase == "ship"):
        # Iter 332 — skipping at a SHIP gate must NOT resume the
        # pipeline (that re-runs EXECUTE and re-hits the same gate →
        # infinite loop). Terminate gracefully instead.
        await engine.skip_at_ship()
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
                      request: Request,
                      authorization: Optional[str] = Header(None),
                      last_event_id: Optional[str] = Header(
                          None, alias="Last-Event-ID",
                      )):
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
    # See module-level STREAM_MAX_S (Iter 282, Release It! Governor).
    _STREAM_MAX_S = STREAM_MAX_S

    # Iter 309 · Batch-2 Item 6 — parse Last-Event-ID for replay.
    # `last_event_id` is the FastAPI-parsed header (browsers set it
    # automatically on reconnect); a query-string fallback is also
    # honored for hand-crafted curl reconnect testing.
    from services import sse_replay_buffer as _sse_buf
    lei_hdr = last_event_id or request.query_params.get("last_event_id") or ""
    replay_after_seq = _sse_buf.parse_last_event_id(lei_hdr, loop_id)

    async def gen():
        db = get_db()
        sent_sig = None
        _stream_started = time.monotonic()
        # Iter 309 · Batch-2 Item 6 — set the browser reconnect
        # backoff on OUR terms (default varies by browser; Chrome
        # is 3s but Safari can be as short as 0ms which hammers
        # the server on cell-network blips).
        yield f"retry: {_sse_buf.BROWSER_RECONNECT_MS}\n\n"

        # Iter 309 · Batch-2 Item 6 — replay buffered events with
        # seq > Last-Event-ID before attaching to the live queue.
        # Zero events replayed when the client is a fresh subscriber
        # (Last-Event-ID absent → replay_after_seq = -1 → replay
        # everything the buffer has, which for a new loop is empty
        # or just the initial PLANNING event — cheap).
        for _seq, _ev in _sse_buf.replay_after(loop_id, replay_after_seq):
            _sig = (_ev.get("ts"), _ev.get("state"),
                    _ev.get("phase"), _ev.get("message"))
            sent_sig = _sig
            _ev_id = f"{loop_id}:{_seq}"
            yield f"id: {_ev_id}\ndata: {json.dumps(_ev)}\n\n"
        try:
            while True:
                if time.monotonic() - _stream_started > _STREAM_MAX_S:
                    _cap_min = _STREAM_MAX_S // 60
                    # Use a distinct state — NOT "aborted" — so the
                    # frontend's onTerminal handler (which triggers on
                    # completed/failed/aborted) does NOT fire and lie
                    # to the user that the loop finished. The loop's
                    # actual engine task keeps running in the background;
                    # this cap only disconnects THIS SSE client. User
                    # can reconnect via GET /loop/{id}/stream and the
                    # ring buffer will replay everything they missed.
                    terminal_ev = {
                        "state":   "stream_capped",
                        "phase":   "?",
                        "message": (
                            f"SSE stream capped at {_cap_min} min — the "
                            "loop is still running on the backend. "
                            "Reconnect to /loop/{id}/stream to keep "
                            "watching."
                        ),
                        "ts": time.time(),
                    }
                    # Cap-notice does NOT get a stable id — it's a
                    # transport signal, not a loop event; if the
                    # client reconnects it will replay from the
                    # last REAL event's Last-Event-ID.
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
                    # Iter 309 · Batch-2 Item 6 (bug_verify_315 fix) —
                    # the buffer is recorded by LoopEngine._emit at the
                    # PRODUCER side, not here.  Find the seq that
                    # _emit already assigned to this event so we can
                    # emit the matching `id:` line.
                    _ev_id = None
                    for _s, _bev in reversed(list(
                            _sse_buf._BUFFERS.get(loop_id, _sse_buf._LoopBuf()).events)):
                        if _bev is ev:
                            _ev_id = f"{loop_id}:{_s}"
                            break
                    if _ev_id:
                        yield f"id: {_ev_id}\ndata: {json.dumps(ev)}\n\n"
                    else:
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
                    {"_id": 0, "last_event": 1, "state": 1,
                     "rollback_status": 1})
                if not doc:
                    break
                mev = doc.get("last_event") or {}
                sig = (mev.get("ts"), mev.get("state"),
                       mev.get("phase"), mev.get("message"))
                if mev and sig != sent_sig:
                    sent_sig = sig
                    # Producer-side record for cross-worker case: if
                    # engine is on ANOTHER worker, this worker never
                    # emits so _emit's record didn't fire here.  Add
                    # to local buffer for consistency.
                    _seq, _ev_id = _sse_buf.record(loop_id, mev)
                    yield f"id: {_ev_id}\ndata: {json.dumps(mev)}\n\n"
                else:
                    yield ": keepalive\n\n"
                if (doc.get("state") or "").lower() in _TERMINAL:
                    # Iter 330 — hold stream open ONLY while a rollback
                    # is in flight. Rollback emits reach this poll via
                    # `services.loop_rollback._emit_rollback_event`
                    # writing `last_event` on the same session doc.
                    # Once rollback reaches done/failed OR never
                    # started, break normally — original behaviour.
                    # STREAM_MAX_S hard cap remains as belt-and-braces
                    # so a caller who opens the stream and never fires
                    # the /rollback POST cannot leak past ~20 min.
                    _rb = str(doc.get("rollback_status") or "").lower()
                    if _rb in ("queued", "running"):
                        continue
                    break
        finally:
            # Iter 309 · Batch-2 Item 6 (bug_verify_315 fix) — do NOT
            # deregister the engine on client disconnect. The engine
            # may still be running; tying its lifecycle to a single
            # SSE client's TCP session breaks reconnect (the next
            # `open` would find engine=None → Mongo fallback → miss
            # intermediate gap events). Engines self-deregister on
            # terminal transitions inside `_do_ship` / `_fail`.
            pass

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


# ── Iter 329 · Deploy 3-A — Loop Rollback ───────────────────────────
#
# Give loop-mode a REAL, non-force-push, history-preserving revert of
# the shipped commit. Pre-Iter-329 the ShipConfirmModal's Rollback
# button called `/cto/tasks/{task_id}/rollback` — but loop mode never
# creates cto_tasks rows, so `taskId` stayed null and the button
# silently did nothing for every loop-mode ship. This endpoint fixes
# that by operating directly off `loop_sessions.context.commit`.
#
# Contract:
#   • Only the loop's owner may roll back.
#   • Loop must be in COMPLETED state with a `context.commit.full_sha`.
#   • Client must echo `confirm="ROLLBACK"` — same double-safety as
#     the legacy `/cto/tasks/{id}/rollback` endpoint.
#   • Persistence: writes `rollback_status="queued"|"running"|"done"
#     |"failed"`, `rollback_sha`, `rollback_html_url`, `rollback_error`,
#     `rollback_started_at`, `rollback_completed_at`, `rollback_steps[]`
#     onto the `loop_sessions` doc so the LiveFeed can poll it.
#   • Uses `services.loop_rollback.run_rollback` (background task) →
#     reuses `github_api_writer.revert_commit` — same workhorse
#     `_run_rollback_via_api` uses for cto_tasks path. Zero divergence.

class LoopRollbackBody(BaseModel):
    """Client must echo 'ROLLBACK' to confirm intent server-side too."""
    confirm: str = Field(..., min_length=8, max_length=16)


@router.post("/{loop_id}/rollback")
async def rollback_loop(
    loop_id: str,
    body: LoopRollbackBody,
    bg: BackgroundTasks,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 329 · revert a loop's shipped GitHub commit via a NEW
    revert commit on the branch. Non-force-push, history preserved.

    Returns {ok, loop_id, rollback_status, commit_sha}. Progress is
    persisted on the `loop_sessions` doc for polling."""
    user = await current_dev(authorization)
    if (body.confirm or "").strip().upper() != "ROLLBACK":
        raise HTTPException(400, "Must confirm with 'ROLLBACK'")

    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    sess = await db.loop_sessions.find_one(
        {"loop_id": loop_id, "user_id": user["user_id"]},
    )
    if not sess:
        raise HTTPException(404, "Loop not found")

    state = sess.get("state")
    if state != "completed":
        raise HTTPException(
            400,
            f"Only completed loops can be rolled back (current: {state})",
        )
    commit = (sess.get("context") or {}).get("commit") or {}
    full_sha = commit.get("full_sha") or commit.get("sha")
    if not full_sha:
        raise HTTPException(400, "Loop has no shipped commit to revert")
    # Idempotence — refuse if already rolled back or in flight.
    rb_status = sess.get("rollback_status")
    if sess.get("rollback_sha"):
        raise HTTPException(409, "Loop already rolled back")
    if rb_status in ("queued", "running"):
        raise HTTPException(409, "Rollback already in progress")
    if rb_status == "failed":
        raise HTTPException(
            409, "Previous rollback failed — manual intervention required",
        )

    project_id = sess.get("project_id")
    if not project_id:
        raise HTTPException(
            400, "Loop is not linked to a project — cannot resolve repo/PAT",
        )
    # Fetch project + PAT via the same path cto_tasks rollback uses so
    # we get identical scoping + encryption semantics.
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user["user_id"]},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0},
    )
    if not proj:
        raise HTTPException(404, "Parent project not found")

    # Reuse the cto_projects PAT decrypt helper so we don't create a
    # second key-derivation path (single source of truth for HKDF).
    from routers.cto_projects import _decrypt_pat, _user_gh_token
    user_token = await _decrypt_pat(user["user_id"], proj.get("github_token")) \
        or await _user_gh_token(user["user_id"])
    if not user_token:
        raise HTTPException(
            400,
            "No PAT on file for this project — open Projects → Edit and "
            "add one.",
        )

    await db.loop_sessions.update_one(
        {"loop_id": loop_id},
        {"$set": {
            "rollback_status":     "queued",
            "rollback_started_at": time.time(),
            "rollback_commit_sha": full_sha,
        }},
    )

    from services.loop_rollback import run_rollback
    bg.add_task(
        run_rollback,
        db=db, loop_id=loop_id, project=proj,
        commit_sha=full_sha, user_token=user_token,
    )

    return {
        "ok":              True,
        "loop_id":         loop_id,
        "rollback_status": "queued",
        "commit_sha":      full_sha,
    }


# ── Iter 330 — Operation history for auto-collapsing UI ──────────────
#
# Frontend `OperationHistory` renders a stacked timeline of past ship/
# rollback ops per project. This endpoint returns up to `limit` recent
# terminal-state loop sessions, splitting each into 1-2 items:
#   • one "ship" item for the loop's terminal ship state
#   • one additional "rollback" item if `rollback_status` is set
#
# Sorted newest-first by `updated_at`. Query capped at `limit*2`
# candidate sessions since each may yield up to 2 items; we then
# truncate the flattened item list to exactly `limit`.
#
# Read-only. Auth: same `current_dev` pattern as every other
# /loop endpoint. Scoped to `user_id + project_id` so users cannot
# read another user's history.
@router.get("/history")
async def loop_history(
    project_id: str,
    limit: int = 20,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    if not project_id or len(project_id) > 200:
        raise HTTPException(400, "project_id required")
    lim = max(1, min(int(limit or 20), 100))

    # Fetch up to lim*2 candidates — some produce 2 items, some 1.
    cursor = db.loop_sessions.find(
        {
            "user_id":    user["user_id"],
            "project_id": project_id,
            "state":      {"$in": ["completed", "failed", "aborted"]},
        },
        {
            "_id":                    0,
            "loop_id":                1,
            "state":                  1,
            "created_at":             1,
            "updated_at":             1,
            "context.commit":         1,
            "rollback_status":        1,
            "rollback_started_at":    1,
            "rollback_completed_at":  1,
            "rollback_sha":           1,
            "rollback_html_url":      1,
            "rollback_error":         1,
            "rollback_steps":         1,
            "error":                  1,
        },
    ).sort("updated_at", -1).limit(lim * 2)

    def _iso_or_ts(v):
        # Sessions store timestamps in mixed formats (ISO string from
        # engine emits, float from rollback code paths). Normalize to
        # ISO for the frontend.
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        return str(v)

    items: list = []
    async for sess in cursor:
        commit = ((sess.get("context") or {}).get("commit")) or {}
        ship_sha  = commit.get("full_sha") or commit.get("sha")
        ship_url  = commit.get("html_url")
        loop_id   = sess.get("loop_id")
        session_state = sess.get("state")

        # (1) Ship item — always emitted for a terminal loop session.
        items.append({
            "loop_id":      loop_id,
            "op_type":      "ship",
            "state":        session_state,
            "started_at":   _iso_or_ts(sess.get("created_at")),
            "finished_at": _iso_or_ts(sess.get("updated_at")),
            "final_status": session_state,
            "all_passed":   session_state == "completed",
            "step_count":   0,  # ship step_count not tracked in schema; frontend renders 0 as "—"
            "commit_sha":   ship_sha,
            "html_url":     ship_url,
            "error":        sess.get("error") if session_state != "completed" else None,
            "steps":        [],
        })

        # (2) Rollback item — only if a rollback was ever fired.
        rb_status = sess.get("rollback_status")
        if rb_status:
            rb_steps_raw = sess.get("rollback_steps") or []
            rb_steps = [
                {
                    "label":  s.get("step", ""),
                    "status": (
                        "failed" if (s.get("status") == "error")
                        else "done"
                    ),
                }
                for s in rb_steps_raw
            ]
            # Map rollback_status → frontend `state` enum values.
            rb_terminal = {
                "done":    "completed",
                "failed":  "failed",
                "queued":  "running",
                "running": "running",
            }.get(str(rb_status).lower(), "running")
            items.append({
                "loop_id":      loop_id,
                "op_type":      "rollback",
                "state":        rb_terminal,
                "started_at":   _iso_or_ts(sess.get("rollback_started_at")),
                "finished_at": _iso_or_ts(sess.get("rollback_completed_at")),
                "final_status": rb_terminal,
                "all_passed":   rb_terminal == "completed",
                "step_count":   len(rb_steps),
                "commit_sha":   sess.get("rollback_sha"),
                "html_url":     sess.get("rollback_html_url"),
                "error":        sess.get("rollback_error"),
                "steps":        rb_steps,
            })

    # Truncate to caller's requested limit AFTER flattening.
    return {"ok": True, "items": items[:lim]}


# ── Iter 282 — deploy-verification diagnostics endpoint ─────────────
#
# Founder-only introspection of the ACTUAL runtime values so we can
# prove-by-inspection (not by "should be on the same ref") that
# Iter 282's Governor + Steady State patches are live in production.
# Reads:
#   • the running process's actual STREAM_MAX_S constant
#   • the actual index_information() from prod Mongo for every
#     loop-machinery collection, filtered to only those carrying
#     `expireAfterSeconds`
# No hardcoded expected values — this is real proof.
@router.get("/_diagnostics")
async def loop_diagnostics(
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    if not (user.get("is_admin")
            or user.get("is_unlimited")
            or (user.get("tier") or "").lower() == "founder"):
        raise HTTPException(403, "founder access required")

    db = get_db()
    if db is None:
        raise HTTPException(503, "DB unavailable")

    ttl_report: dict = {}
    for coll in _TTL_MANAGED_COLLECTIONS:
        try:
            idxs = await db[coll].index_information()
        except Exception as e:                                # noqa: BLE001
            ttl_report[coll] = {"error": repr(e)[:200]}
            continue
        ttl_entries = []
        for name, info in idxs.items():
            if "expireAfterSeconds" not in info:
                continue
            ttl_entries.append({
                "name":                 name,
                "key":                  [list(kv) for kv in info.get("key", [])],
                "expireAfterSeconds":   info["expireAfterSeconds"],
            })
        ttl_report[coll] = ttl_entries

    ttl_present = sorted(
        c for c, entries in ttl_report.items()
        if isinstance(entries, list) and entries
    )

    return {
        "ok":                       True,
        "iter":                     282,
        "stream_max_s":             STREAM_MAX_S,
        "ttl_indexes_present":      ttl_present,
        "ttl_indexes_detail":       ttl_report,
        "db_name":                  db.name,
    }
