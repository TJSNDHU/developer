"""
routers/chat.py — AUREM Dev
AI chat endpoints: send (sync), stream (SSE), history, sessions.
All messages persisted to db.chat_sessions per user.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.orchestrator import chat_with_tools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


class ChatBody(BaseModel):
    prompt: str
    session_id: Optional[str] = None
    max_tool_iters: int = 2


async def _persist_turn(user_id: str, session_id: str, user_prompt: str,
                        assistant_reply: str, provider: str) -> None:
    """Append user+assistant turns to db.chat_sessions, capped at 40 turns."""
    db = get_db()
    if db is None or not session_id:
        return
    now = time.time()
    preview = (assistant_reply or "").strip()[:120] or (user_prompt or "")[:120]
    try:
        await db.chat_sessions.update_one(
            {"session_id": session_id, "user_id": user_id},
            {
                "$setOnInsert": {
                    "session_id": session_id,
                    "user_id": user_id,
                    "created_at": now,
                },
                "$set": {
                    "updated_at": now,
                    "last_message": preview,
                },
                "$push": {
                    "turns": {
                        "$each": [
                            {"role": "user", "content": user_prompt, "ts": now},
                            {"role": "assistant", "content": assistant_reply,
                             "ts": now, "provider": provider},
                        ],
                        "$slice": -40,
                    }
                },
            },
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"persist_turn failed: {e!r}")


@router.post("/send")
async def chat_send(
    body: ChatBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Non-streaming chat — returns full response, persists turn."""
    user = await current_dev(authorization)
    jwt_token = authorization.split(" ", 1)[1] if authorization else ""
    result = await chat_with_tools(
        prompt=body.prompt,
        jwt_token=jwt_token,
        max_iters=min(body.max_tool_iters, 6),
        session_id=body.session_id,
        mongo_client=None,
    )
    content = result.get("content", "") or ""
    provider = result.get("provider", "") or ""
    await _persist_turn(user["user_id"], body.session_id or "",
                        body.prompt, content, provider)
    return {
        "ok": result.get("ok", True),
        "content": content,
        "provider": provider,
        "iterations": result.get("iterations", 0),
        "session_id": body.session_id,
        "user_id": user.get("user_id"),
    }


@router.post("/stream")
async def chat_stream(
    body: ChatBody,
    authorization: Optional[str] = Header(None),
):
    """SSE token-streaming chat. Emits `data: {"token":"..."}` per chunk
    then `data: {"done":true,"provider":"..."}`. Persists final turn."""
    user = await current_dev(authorization)
    jwt_token = authorization.split(" ", 1)[1] if authorization else ""
    user_id = user.get("user_id", "")

    async def gen():
        # Compute full response first (orchestrator + tool loop are not
        # token-streamable today). Then chunk it back as SSE so the UI
        # gets the live-typing UX without coupling to provider streams.
        try:
            result = await chat_with_tools(
                prompt=body.prompt,
                jwt_token=jwt_token,
                max_iters=min(body.max_tool_iters, 6),
                session_id=body.session_id,
                mongo_client=None,
            )
        except Exception as e:
            logger.exception("chat_stream orchestrator failed")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        content = result.get("content", "") or ""
        provider = result.get("provider", "") or ""

        # Emit session_id + provider first so the client can pin them
        meta = {"meta": True, "session_id": body.session_id, "provider": provider}
        yield f"data: {json.dumps(meta)}\n\n"

        # Stream content in ~6-char chunks at ~15ms cadence
        CHUNK = 6
        i = 0
        while i < len(content):
            chunk = content[i:i + CHUNK]
            yield f"data: {json.dumps({'token': chunk})}\n\n"
            i += CHUNK
            await asyncio.sleep(0.012)

        # Persist + done
        await _persist_turn(user_id, body.session_id or "",
                            body.prompt, content, provider)
        done_payload = {
            "done": True,
            "provider": provider,
            "session_id": body.session_id,
        }
        yield f"data: {json.dumps(done_payload)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/history")
async def chat_history(
    session_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Return last 20 turns of a session for the current user."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None or not session_id:
        return {"ok": True, "messages": [], "session_id": session_id}
    doc = await db.chat_sessions.find_one(
        {"session_id": session_id, "user_id": user["user_id"]},
        {"_id": 0, "turns": 1},
    )
    turns = ((doc or {}).get("turns") or [])[-20:]
    return {"ok": True, "messages": turns, "session_id": session_id}


@router.get("/sessions")
async def chat_sessions_list(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Return up to 20 most-recent chat sessions for the current user."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True, "sessions": []}
    cursor = db.chat_sessions.find(
        {"user_id": user["user_id"]},
        {"_id": 0, "session_id": 1, "last_message": 1, "updated_at": 1, "created_at": 1},
    ).sort("updated_at", -1).limit(20)
    sessions = await cursor.to_list(length=20)
    return {"ok": True, "sessions": sessions}


@router.delete("/sessions/{session_id}")
async def chat_session_delete(
    session_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Delete a single chat session belonging to the current user."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    r = await db.chat_sessions.delete_one(
        {"session_id": session_id, "user_id": user["user_id"]}
    )
    return {"ok": True, "deleted": r.deleted_count}
