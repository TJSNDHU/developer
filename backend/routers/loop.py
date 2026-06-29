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

    engine = eng.LoopEngine(
        db=db, loop_id=loop_id,
        user_id=user["user_id"],
        project_id=body.project_id,
        user_message=body.user_message,
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
    engine = eng.lookup(loop_id)
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
    engine = eng.lookup(loop_id)
    if engine is None:
        raise HTTPException(404, "Loop not found or already finished")
    if engine.user_id != user["user_id"]:
        raise HTTPException(403, "Not your loop")
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
    engine = eng.lookup(loop_id)
    if engine is None:
        raise HTTPException(404, "Loop not found")
    if engine.user_id != user["user_id"]:
        raise HTTPException(403, "Not your loop")
    if body.action == "abort":
        await engine.cancel()
    elif body.action in ("retry", "skip"):
        # Phase C implements true retry/skip semantics; Phase B simply
        # resumes the pipeline from the next phase.
        engine.state = eng.LoopState.EXECUTING
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
    engine = eng.lookup(loop_id)
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
    if engine is None:
        raise HTTPException(404, "Loop not active in this worker")
    if engine.user_id != user["user_id"]:
        raise HTTPException(403, "Not your loop")

    async def gen():
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(engine.queue.get(), 30.0)
                except asyncio.TimeoutError:
                    # Heartbeat so proxies don't kill the connection.
                    yield ": keepalive\n\n"
                    if engine.state in {eng.LoopState.COMPLETED,
                                        eng.LoopState.FAILED,
                                        eng.LoopState.ABORTED}:
                        break
                    continue
                yield f"data: {json.dumps(ev)}\n\n"
                if engine.state in {eng.LoopState.COMPLETED,
                                    eng.LoopState.FAILED,
                                    eng.LoopState.ABORTED}:
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
    engine = eng.lookup(loop_id)
    if engine is None:
        # Already finished or never existed — try Mongo lookup so we
        # can mark it aborted persistently.
        db = get_db()
        if db is not None:
            doc = await eng.load_session(db, loop_id)
            if doc and doc.get("user_id") == user["user_id"]:
                await db.loop_sessions.update_one(
                    {"loop_id": loop_id},
                    {"$set": {"state": "aborted"}},
                )
                return {"loop_id": loop_id, "state": "aborted"}
        raise HTTPException(404, "Loop not found")
    if engine.user_id != user["user_id"]:
        raise HTTPException(403, "Not your loop")
    await engine.cancel()
    return {"loop_id": loop_id, "state": engine.state.value}
