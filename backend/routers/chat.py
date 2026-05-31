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
from services.repo_context import get_repo_context
from services.url_fetcher import build_url_context

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
    project_id: Optional[str] = None


_TITLE_SYSTEM = "Generate ultra-short chat titles. 3-5 words, Title Case, no punctuation. Just the title."


async def _generate_title(first_user_msg: str) -> str:
    """Ask the LLM to summarize the first user message in 3-5 words.
    Returns "" on any failure so the caller can fall back to last_message."""
    try:
        prompt = f"3-5 word title, Title Case, no punctuation: {first_user_msg.strip()[:100]}"
        meta = await call_llm_with_meta(_TITLE_SYSTEM, prompt,
                                         max_tokens=cap_for("title"),
                                         mode="title")
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
                        watchdog: Optional[dict] = None,
                        project_id: Optional[str] = None) -> None:
    """Append user+assistant turns to db.chat_sessions, capped at 40 turns.
    Tags the session with the project it belongs to (None == Home/global)."""
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
    set_on_insert = {
        "session_id": session_id,
        "user_id": user_id,
        "created_at": now,
        "project_id": project_id,
    }
    set_fields = {
        "updated_at": now,
        "last_message": preview,
    }
    try:
        await db.chat_sessions.update_one(
            {"session_id": session_id, "user_id": user_id},
            {
                "$setOnInsert": set_on_insert,
                "$set": set_fields,
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
    repo_ctx = await get_repo_context(user["user_id"], body.project_id or "")
    url_ctx = await build_url_context(body.prompt)
    extra_sys = "\n\n".join(s for s in (repo_ctx, url_ctx) if s)
    result = await chat_with_tools(
        prompt=body.prompt,
        jwt_token=jwt_token,
        system=(extra_sys + "\n\n" if extra_sys else None),
        max_iters=min(body.max_tool_iters, 6),
        session_id=body.session_id,
        mongo_client=None,
        user_id=user["user_id"],
        project_id=body.project_id,
    )
    content = result.get("content", "") or ""
    provider = result.get("provider", "") or ""
    mode = _detect_mode(body.prompt)
    from services.llm import temperature_for
    temperature = temperature_for(mode)

    # Maxx mode: watchdog review (only if we have non-empty content)
    watchdog = None
    if body.maxx_mode and content.strip():
        watchdog = await call_emergent_watchdog(content)
        provider = (provider or "deepseek") + "+emergent-watchdog"

    await _persist_turn(user["user_id"], body.session_id or "",
                        body.prompt, content, provider, watchdog=watchdog,
                        project_id=body.project_id)
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
        "mode": mode,
        "temperature": temperature,
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
    repo_ctx = await get_repo_context(user_id, body.project_id or "")
    url_ctx = await build_url_context(body.prompt)
    extra_sys = "\n\n".join(s for s in (repo_ctx, url_ctx) if s)

    async def gen():
        try:
            result = await chat_with_tools(
                prompt=body.prompt,
                jwt_token=jwt_token,
                system=(extra_sys + "\n\n" if extra_sys else None),
                max_iters=min(body.max_tool_iters, 6),
                session_id=body.session_id,
                mongo_client=None,
                user_id=user_id,
                project_id=body.project_id,
            )
        except Exception as e:
            logger.exception("chat_stream orchestrator failed")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        content = result.get("content", "") or ""
        provider = result.get("provider", "") or ""
        mode = _detect_mode(body.prompt)
        from services.llm import temperature_for
        temperature = temperature_for(mode)

        meta = {"meta": True, "session_id": body.session_id,
                "provider": provider, "mode": mode, "temperature": temperature}
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
                            body.prompt, content, provider, watchdog=watchdog,
                            project_id=body.project_id)
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
    project_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Return up to 20 most-recent chat sessions for the current user.
    Filter to a specific project_id when provided; pass 'home' to get
    sessions that aren't bound to any project."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True, "sessions": []}
    q = {"user_id": user["user_id"]}
    if project_id == "home":
        # Home tab shows un-pinned sessions PLUS legacy sessions that have
        # no project_id field at all (created before per-project chats).
        q["$or"] = [{"project_id": None}, {"project_id": {"$exists": False}}]
    elif project_id:
        q["project_id"] = project_id
    cursor = db.chat_sessions.find(
        q,
        {
            "_id": 0, "session_id": 1, "title": 1, "project_id": 1,
            "last_message": 1, "updated_at": 1, "created_at": 1,
        },
    ).sort("updated_at", -1).limit(20)
    sessions = await cursor.to_list(length=20)
    return {"ok": True, "sessions": sessions}


class TurnShippedBody(BaseModel):
    session_id: str
    turn_index: int
    task_id: str


@router.post("/turn/shipped")
async def chat_turn_shipped(
    body: TurnShippedBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Record that an assistant turn was shipped via CTO so the Ship button
    doesn't re-appear on refresh/rejoin. Stores `task_id` on the turn doc.

    Iter 34 — defensive validation: refuse to write past the end of the
    turns array. MongoDB silently creates sparse `turns[N]` entries when
    asked to $set on an out-of-range index, which corrupts the document
    and brings the Ship button back on every refresh. Front-end already
    sends a DB-correct index, but legacy clients / stale tabs might not.
    """
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    if body.turn_index < 0:
        raise HTTPException(400, "turn_index must be >= 0")

    # Look up the live turn count before we write
    sess = await db.chat_sessions.find_one(
        {"session_id": body.session_id, "user_id": user["user_id"]},
        {"_id": 0, "turns": 1},
    )
    if not sess:
        raise HTTPException(404, "Session not found")
    turns = sess.get("turns") or []
    if body.turn_index >= len(turns):
        # Off-by-one or stale index — don't corrupt the doc. Fall back to
        # marking the latest assistant turn as shipped (safest default).
        last_asst = max(
            (i for i, t in enumerate(turns) if (t or {}).get("role") == "assistant"),
            default=None,
        )
        if last_asst is None:
            raise HTTPException(409,
                                "Cannot record shipped state — no assistant "
                                "turns in this session yet")
        body = TurnShippedBody(session_id=body.session_id,
                               turn_index=last_asst,
                               task_id=body.task_id)

    set_field = f"turns.{body.turn_index}.shipped_task_id"
    await db.chat_sessions.update_one(
        {"session_id": body.session_id, "user_id": user["user_id"]},
        {"$set": {set_field: body.task_id}},
    )
    return {"ok": True, "turn_index": body.turn_index}


class FeedbackBody(BaseModel):
    session_id: str
    turn_index: int       # index within the turns array (assistant turn)
    vote: str             # 'up' | 'down'
    comment: Optional[str] = None


@router.post("/feedback")
async def chat_feedback(
    body: FeedbackBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Record like/dislike on an assistant turn. Used for future fine-tuning
    + lets the UI show that feedback was captured."""
    user = await current_dev(authorization)
    if body.vote not in ("up", "down"):
        raise HTTPException(400, "vote must be 'up' or 'down'")
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    set_field = f"turns.{body.turn_index}.feedback"
    await db.chat_sessions.update_one(
        {"session_id": body.session_id, "user_id": user["user_id"]},
        {"$set": {set_field: {
            "vote": body.vote,
            "comment": body.comment,
            "ts": time.time(),
        }}},
    )
    return {"ok": True}


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
