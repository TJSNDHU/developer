"""
services/ora_client.py — Thin client for aurem.live's ORA chat API.

Contract (verified by founder 2026-06-01):
  POST {ORA_BASE_URL}/api/v1/public/ora/chat
  Headers: Authorization: Bearer ${ORA_API_KEY}
  Body:    {message, session_id?, system_hint?}
  Success: 200 {ok, reply, session_id, tier, model}
  Errors:  401/403/429/500 with FastAPI {detail} shape

Founder-only: only users in the FOUNDER_EMAILS allow-list (services.usage)
can select ORA. The API key is shared across all founders so we never
need to surface it client-side.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx
from fastapi import HTTPException


def is_ora_available() -> bool:
    return bool(os.environ.get("ORA_API_KEY", "").strip())


async def call_ora(
    message: str,
    session_id: Optional[str] = None,
    system_hint: Optional[str] = None,
    scope: str = "ora",            # "ora" → /ora/chat, "cto" → /cto/chat
    timeout: float = 60.0,
) -> dict:
    api_key = os.environ.get("ORA_API_KEY", "").strip()
    base = os.environ.get("ORA_BASE_URL", "https://aurem.live").rstrip("/")
    if not api_key:
        raise HTTPException(503, "ORA not configured on this deployment")
    path = "/api/v1/public/ora/chat" if scope == "ora" else "/api/v1/public/cto/chat"
    body: dict = {"message": (message or "").strip()[:4000]}
    if session_id:
        body["session_id"] = session_id[:128]
    if system_hint:
        body["system_hint"] = system_hint[:2000]
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(
                base + path,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json=body,
            )
    except httpx.TimeoutException:
        raise HTTPException(504, f"ORA upstream timed out after {timeout}s")
    except Exception as e:
        raise HTTPException(502, f"ORA upstream error: {type(e).__name__}")
    if r.status_code == 200:
        return r.json()
    # Surface upstream detail verbatim so the user sees the real error
    try:
        detail = r.json().get("detail", r.text[:200])
    except Exception:
        detail = r.text[:200]
    raise HTTPException(r.status_code, f"ORA: {detail}")
