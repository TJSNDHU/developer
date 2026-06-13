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

Iter 107 — Persistent circuit breaker.
  When upstream returns a known-broken error (OpenRouter model deprecation
  on aurem.live, persistent 5xx), we open the circuit for 1 hour and
  short-circuit subsequent calls without making any HTTP request. This
  eliminates the noisy `aurem.live 500` log spam the founder was seeing
  whenever aurem.live's internal model slug breaks. The breaker state
  lives in /tmp so fresh uvicorn workers in the same pod also skip the
  bad upstream immediately on startup. Self-heals after the cool-down.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# ── Circuit breaker config ────────────────────────────────────
# Iter 141 — default lowered from 3600s → 600s (10 min) and made
# env-configurable. The "model unavailable for free" failure mode is a
# config-side issue on the aurem.live upstream that an operator fixes
# manually; 10 minutes is enough to silence the retry storm without
# stranding the app for an hour. Override via ORA_BREAKER_COOLDOWN_S.
_BREAKER_COOLDOWN_SECS = int(os.getenv("ORA_BREAKER_COOLDOWN_S", "600"))
_BREAKER_FILE = Path("/tmp/aurem_ora_circuit_open")

# Substrings of upstream errors that are NEVER going to fix themselves via
# retry — opening the breaker the moment we see them is correct.
_FATAL_UPSTREAM_PATTERNS = (
    "openrouter HTTP 404",
    "model is unavailable",
    "ora_chat_error",
    "openrouter HTTP 401",
    "openrouter HTTP 403",
)


def _breaker_is_open() -> bool:
    """True iff the breaker file exists AND is within cool-down window."""
    try:
        if not _BREAKER_FILE.exists():
            return False
        age = time.time() - _BREAKER_FILE.stat().st_mtime
        if age < _BREAKER_COOLDOWN_SECS:
            return True
        # Cool-down expired — clear and let the next call probe upstream.
        try:
            _BREAKER_FILE.unlink()
        except OSError:
            pass
        return False
    except Exception:
        return False


def _trip_breaker(reason: str) -> None:
    """Open the breaker. Persists across workers in this pod."""
    try:
        _BREAKER_FILE.write_text(f"{int(time.time())} {reason[:200]}\n")
        logger.info("ORA upstream circuit OPEN for %ds — reason: %s",
                    _BREAKER_COOLDOWN_SECS, reason[:200])
    except OSError as e:
        logger.warning("failed to persist ORA breaker file: %r", e)


def is_ora_available() -> bool:
    return bool(os.environ.get("ORA_API_KEY", "").strip()) and not _breaker_is_open()


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

    # Iter 107 — short-circuit if the breaker is open. No HTTP call, no
    # log spam. The caller (routers/chat.py) already falls back to local
    # AUREM on any HTTPException from this function.
    if _breaker_is_open():
        raise HTTPException(503, "ORA upstream temporarily unavailable (circuit open)")

    path = "/api/v1/public/ora/chat" if scope == "ora" else "/api/v1/public/cto/chat"
    body: dict = {"message": (message or "").strip()[:4000]}
    if session_id:
        body["session_id"] = session_id[:128]
    if system_hint:
        # aurem.live upstream rejects system_hint > 400 chars with 422.
        # Cap defensively at 380 to leave headroom.
        body["system_hint"] = system_hint[:380]
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
        # Persistent timeouts → trip the breaker so we don't keep adding
        # 60s of latency to every retry while upstream is down.
        _trip_breaker("upstream_timeout")
        raise HTTPException(504, f"ORA upstream timed out after {timeout}s")
    except Exception as e:
        _trip_breaker(f"transport_{type(e).__name__}")
        raise HTTPException(502, f"ORA upstream error: {type(e).__name__}")
    if r.status_code == 200:
        return r.json()
    # Surface upstream detail verbatim so the user sees the real error
    try:
        detail = r.json().get("detail", r.text[:200])
    except Exception:
        detail = r.text[:200]

    # Iter 107 — Trip the breaker on:
    #   (a) any 5xx (server-side problem on aurem.live — retrying won't help)
    #   (b) any fatal-pattern body text (OpenRouter model deprecation etc.)
    if r.status_code >= 500 or any(p.lower() in str(detail).lower()
                                    for p in _FATAL_UPSTREAM_PATTERNS):
        _trip_breaker(f"http_{r.status_code}: {str(detail)[:100]}")

    raise HTTPException(r.status_code, f"ORA: {detail}")
