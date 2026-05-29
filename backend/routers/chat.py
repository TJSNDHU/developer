"""
routers/chat.py — AUREM Dev
AI chat endpoint wired to orchestrator + skills.
"""
from __future__ import annotations
import logging
from typing import Optional

from fastapi import APIRouter, Header
from pydantic import BaseModel

from cto_services.auth import current_dev
from services.orchestrator import chat_with_tools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


class ChatBody(BaseModel):
    prompt: str
    session_id: Optional[str] = None
    max_tool_iters: int = 4


@router.post("/send")
async def chat_send(
    body: ChatBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Non-streaming chat — returns full response."""
    user = await current_dev(authorization)
    jwt_token = authorization.split(" ", 1)[1] if authorization else ""
    result = await chat_with_tools(
        prompt=body.prompt,
        jwt_token=jwt_token,
        max_iters=min(body.max_tool_iters, 6),
        session_id=body.session_id,
        mongo_client=None,
    )
    return {
        "ok": result.get("ok", True),
        "content": result.get("content", ""),
        "provider": result.get("provider", ""),
        "iterations": result.get("iterations", 0),
        "session_id": body.session_id,
        "user_id": user.get("user_id"),
    }


@router.get("/history")
async def chat_history(
    session_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Get chat history for a session."""
    await current_dev(authorization)
    return {"ok": True, "messages": [], "session_id": session_id}
