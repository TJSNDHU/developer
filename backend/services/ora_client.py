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
# Iter 153 — when the upstream returns a known-fatal config error
# ("model unavailable for free", HTTP 404 from OpenRouter, etc.) there is
# nothing retrying every 10 minutes can fix — an operator has to update
# the model slug on aurem.live. Extend the breaker to 24h in that case
# so the log spam stops until the founder rolls a real fix.
_BREAKER_FATAL_COOLDOWN_SECS = int(os.getenv("ORA_BREAKER_FATAL_COOLDOWN_S", "86400"))
_BREAKER_FILE = Path("/tmp/aurem_ora_circuit_open")
_BREAKER_FATAL_FILE = Path("/tmp/aurem_ora_circuit_open_fatal")

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
        # Iter 153 — fatal-cooldown file (24h) takes precedence. We
        # check it FIRST so a known-bad upstream stays silenced even if
        # the short-cooldown file expired underneath it.
        if _BREAKER_FATAL_FILE.exists():
            age = time.time() - _BREAKER_FATAL_FILE.stat().st_mtime
            if age < _BREAKER_FATAL_COOLDOWN_SECS:
                return True
            try:
                _BREAKER_FATAL_FILE.unlink()
            except OSError:
                pass
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


def _trip_breaker(reason: str, fatal: bool = False) -> None:
    """Open the breaker. Persists across workers in this pod.

    When ``fatal=True`` we use the 24h cooldown file so manual-fix-only
    upstream errors (model unavailable, 404, 401) don't re-log every
    10 minutes. The short cooldown is reserved for transient failures
    (timeouts, transport errors, generic 5xx)."""
    try:
        path = _BREAKER_FATAL_FILE if fatal else _BREAKER_FILE
        path.write_text(f"{int(time.time())} {reason[:200]}\n")
        cooldown = _BREAKER_FATAL_COOLDOWN_SECS if fatal else _BREAKER_COOLDOWN_SECS
        logger.info("ORA upstream circuit OPEN for %ds — reason: %s",
                    cooldown, reason[:200])
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
    # Iter 153 — fatal-pattern matches now extend the cooldown to 24h
    # so the log spam stops until an operator fixes the upstream config.
    detail_l = str(detail).lower()
    is_fatal = any(p.lower() in detail_l for p in _FATAL_UPSTREAM_PATTERNS)
    if is_fatal:
        _trip_breaker(f"http_{r.status_code}: {str(detail)[:100]}", fatal=True)
    elif r.status_code >= 500:
        _trip_breaker(f"http_{r.status_code}: {str(detail)[:100]}")

    raise HTTPException(r.status_code, f"ORA: {detail}")
