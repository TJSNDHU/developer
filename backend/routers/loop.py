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
    loop_id = eng.new_loop_id()
    engine = eng.LoopEngine(
        db=db, loop_id=loop_id,
        user_id=user["user_id"],
        project_id=body.project_id,
        user_message=body.user_message,
    )
    eng.register(engine)
    # Drain the plan-phase generator to completion (it pauses on
    # AWAITING_CONFIRMATION before returning).
    async for _ev in engine.start():
        pass
    return {
        "loop_id":      loop_id,
        "state":        engine.state.value,
        "phase":        engine.phase,
        "plan":         engine.context.get("plan"),
        "requires_user_action": engine.state.value == "awaiting_confirmation",
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
