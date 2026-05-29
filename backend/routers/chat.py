"""
routers/chat.py — AUREM Dev
AI chat endpoints: send (sync), stream (SSE), history, sessions.
All messages persisted to db.chat_sessions per user.
First assistant reply triggers a background title-summarization.
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
from services.llm import call_llm_with_meta, call_emergent_watchdog, cap_for

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


# Heuristic: prompt mentions build/create/fix/write code/etc → bump cap
_CODE_HINTS = ("```", "build", "create", "fix", "write", "implement",
               "function", "class", "refactor", "debug", "snippet", "code")


def _detect_mode(prompt: str) -> str:
    p = (prompt or "").lower()
    return "code" if any(h in p for h in _CODE_HINTS) else "chat"


async def _deduct_tokens(user_id: str, reply: str) -> int:
    """Deduct ~1 token per 3 words from the user's wallet. Returns new balance."""
    db = get_db()
    if db is None or not user_id:
        return 0
    used = max(1, len((reply or "").split()) // 3 + 1)
    try:
        await db.dev_users.update_one(
            {"user_id": user_id},
            {"$inc": {"tokens_remaining": -used}},
        )
        u = await db.dev_users.find_one(
            {"user_id": user_id}, {"_id": 0, "tokens_remaining": 1}
        )
        return int((u or {}).get("tokens_remaining", 0))
    except Exception as e:
        logger.warning(f"deduct_tokens failed: {e!r}")
        return 0


class ChatBody(BaseModel):
    prompt: str
    session_id: Optional[str] = None
    max_tool_iters: int = 2
    maxx_mode: bool = False


_TITLE_SYSTEM = "Generate ultra-short chat titles. 3-5 words, Title Case, no punctuation. Just the title."


async def _generate_title(first_user_msg: str) -> str:
    """Ask the LLM to summarize the first user message in 3-5 words.
    Returns "" on any failure so the caller can fall back to last_message."""
    try:
        prompt = f"3-5 word title, Title Case, no punctuation: {first_user_msg.strip()[:100]}"
        meta = await call_llm_with_meta(_TITLE_SYSTEM, prompt, max_tokens=cap_for("title"))
        title = (meta.get("content") or "").strip()
        title = title.strip("\"'`").rstrip(".!?").strip()
        if not title:
            return ""
        if len(title) > 60:
            title = title[:57].rstrip() + "…"
        return title
    except Exception as e:
        logger.warning(f"title generation failed: {e!r}")
        return ""


async def _maybe_set_title(user_id: str, session_id: str,
                            first_user_msg: str) -> None:
    """If this session has no title yet, generate one and store it.
    Safe to call as a background task (fire-and-forget)."""
    db = get_db()
    if db is None or not session_id:
        return
    try:
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0, "title": 1, "turns": 1},
        )
        if not doc:
            return
        if doc.get("title"):
            return
        if len(doc.get("turns") or []) < 2:
            return
        title = await _generate_title(first_user_msg)
        if not title:
            return
        await db.chat_sessions.update_one(
            {"session_id": session_id, "user_id": user_id},
            {"$set": {"title": title}},
        )
        logger.info(f"titled session {session_id[:8]}…: {title!r}")
    except Exception as e:
        logger.warning(f"_maybe_set_title failed: {e!r}")


async def _persist_turn(user_id: str, session_id: str, user_prompt: str,
                        assistant_reply: str, provider: str,
                        watchdog: Optional[dict] = None) -> None:
    """Append user+assistant turns to db.chat_sessions, capped at 40 turns."""
    db = get_db()
    if db is None or not session_id:
        return
    now = time.time()
    preview = (assistant_reply or "").strip()[:120] or (user_prompt or "")[:120]
    assistant_turn = {
        "role": "assistant", "content": assistant_reply,
        "ts": now, "provider": provider,
    }
    if watchdog:
        assistant_turn["watchdog"] = watchdog
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
                            assistant_turn,
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
    """Non-streaming chat — returns full response, persists turn.
    If maxx_mode=True, runs Emergent watchdog review after DeepSeek reply."""
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

    # Maxx mode: watchdog review (only if we have non-empty content)
    watchdog = None
    if body.maxx_mode and content.strip():
        watchdog = await call_emergent_watchdog(content)
        provider = (provider or "deepseek") + "+emergent-watchdog"

    await _persist_turn(user["user_id"], body.session_id or "",
                        body.prompt, content, provider, watchdog=watchdog)
    if body.session_id:
        asyncio.create_task(
            _maybe_set_title(user["user_id"], body.session_id, body.prompt)
        )
    tokens_remaining = await _deduct_tokens(user["user_id"], content)
    return {
        "ok": result.get("ok", True),
        "content": content,
        "provider": provider,
        "watchdog": watchdog,
        "iterations": result.get("iterations", 0),
        "session_id": body.session_id,
        "user_id": user.get("user_id"),
        "tokens_remaining": tokens_remaining,
    }


@router.post("/stream")
async def chat_stream(
    body: ChatBody,
    authorization: Optional[str] = Header(None),
):
    """SSE token-streaming chat. Emits meta → token(×N) → done frames.
    Persists final turn and triggers background title generation."""
    user = await current_dev(authorization)
    jwt_token = authorization.split(" ", 1)[1] if authorization else ""
    user_id = user.get("user_id", "")

    async def gen():
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

        meta = {"meta": True, "session_id": body.session_id, "provider": provider}
        yield f"data: {json.dumps(meta)}\n\n"

        CHUNK = 6
        i = 0
        while i < len(content):
            chunk = content[i:i + CHUNK]
            yield f"data: {json.dumps({'token': chunk})}\n\n"
            i += CHUNK
            await asyncio.sleep(0.012)

        # Maxx mode: emit a stream marker, then run watchdog and emit result
        watchdog = None
        if body.maxx_mode and content.strip():
            yield f"data: {json.dumps({'watchdog_pending': True})}\n\n"
            watchdog = await call_emergent_watchdog(content)
            yield f"data: {json.dumps({'watchdog': watchdog})}\n\n"
            provider = (provider or "deepseek") + "+emergent-watchdog"

        await _persist_turn(user_id, body.session_id or "",
                            body.prompt, content, provider, watchdog=watchdog)
        if body.session_id:
            asyncio.create_task(
                _maybe_set_title(user_id, body.session_id, body.prompt)
            )
        tokens_remaining = await _deduct_tokens(user_id, content)

        done_payload = {
            "done": True,
            "provider": provider,
            "session_id": body.session_id,
            "tokens_remaining": tokens_remaining,
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
        {"_id": 0, "turns": 1, "title": 1},
    )
    turns = ((doc or {}).get("turns") or [])[-20:]
    return {
        "ok": True,
        "messages": turns,
        "session_id": session_id,
        "title": (doc or {}).get("title", ""),
    }


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
        {
            "_id": 0, "session_id": 1, "title": 1,
            "last_message": 1, "updated_at": 1, "created_at": 1,
        },
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
