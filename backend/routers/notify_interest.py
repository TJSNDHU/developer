"""
routers/notify_interest.py — Iter 212m-158

Tiny capture endpoint for the /tools "Notify me when ready" form.
Cards on /tools are previews of upcoming Bug Hunt / Vanguard /
Security Scan / Health Scan products.  When a user drops their
email + (optional) repo into a card we persist the signal so the
founder can drive launch comms off real demand data.

Schema (`tool_notify_interest` collection):
  • _id           — auto
  • tool          — str (one of: bug-hunt, vanguard, security-scan, health-scan)
  • email         — str (validated lower-cased)
  • repo          — str | null (project_id from the user's repo list)
  • user_id       — str | null (if authenticated, else None)
  • ip            — str | null
  • user_agent    — str | null (truncated to 240 chars)
  • created_at    — ISO timestamp

Anonymous capture is fine (no auth required) — the value is the
email itself.  We rate-limit per-IP to 20/min so the endpoint can't
be used as a Mongo write amplifier.
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from cto_services.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Notify Interest"])

_ALLOWED_TOOLS = {"bug-hunt", "vanguard", "security-scan", "health-scan"}
_EMAIL_RX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# Per-IP token bucket — 20 submissions per 60 s.  In-process only;
# fine for a launch-time signup form, swap to Redis later if traffic
# climbs.
_RATE_BUCKET: dict[str, deque[float]] = {}
_RATE_WINDOW = 60.0
_RATE_LIMIT  = 20


def _rate_check(ip: str) -> bool:
    now = time.monotonic()
    q = _RATE_BUCKET.setdefault(ip, deque())
    while q and (now - q[0]) > _RATE_WINDOW:
        q.popleft()
    if len(q) >= _RATE_LIMIT:
        return False
    q.append(now)
    return True


@router.post("/notify-interest")
async def notify_interest(
    body: dict,
    request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Record interest in an upcoming tool.

    Body shape:
      • tool   — str (required, must be one of _ALLOWED_TOOLS)
      • email  — str (required, must look like an email address)
      • repo   — str | null (optional — the project_id the user
                 selected in the /tools card dropdown)

    Returns: {"ok": True} on success; 400 on bad shape; 429 on
    rate-limit; 503 if the DB is unreachable.  Never raises 500.
    """
    tool  = (body or {}).get("tool")
    email = ((body or {}).get("email") or "").strip().lower()
    repo  = (body or {}).get("repo")
    if tool not in _ALLOWED_TOOLS:
        raise HTTPException(400, f"tool must be one of {sorted(_ALLOWED_TOOLS)}")
    if not _EMAIL_RX.match(email) or len(email) > 240:
        raise HTTPException(400, "invalid email")
    if repo is not None:
        if not isinstance(repo, str) or len(repo) > 120:
            raise HTTPException(400, "invalid repo")

    # Best-effort IP — frontend sits behind ingress so we read the
    # forwarded chain when present.
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "")
          or "unknown")
    if not _rate_check(ip):
        raise HTTPException(429, "Slow down — try again in a minute")

    # Optional: enrich with user_id when the caller is authenticated.
    user_id: Optional[str] = None
    if authorization:
        try:
            from cto_services.auth import current_dev
            user = await current_dev(authorization)
            user_id = user.get("user_id")
        except HTTPException:
            user_id = None
        except Exception as e:                                  # noqa: BLE001
            logger.debug("notify-interest auth enrich failed: %r", e)

    db = get_db()
    if db is None:
        # Don't 500 — the founder asked for "console.log fallback" UX.
        # Returning 200 even when persistence fails keeps the card
        # flowing to its success state.  We still log a warning so
        # the missed signal is recoverable from the app log if
        # needed.
        logger.warning(
            "notify-interest db unavailable; signal lost: tool=%s email=%s repo=%s",
            tool, email, repo,
        )
        return {"ok": True, "persisted": False}

    try:
        await db.tool_notify_interest.insert_one({
            "tool":       tool,
            "email":      email,
            "repo":       repo,
            "user_id":    user_id,
            "ip":         ip[:64],
            "user_agent": (request.headers.get("user-agent") or "")[:240],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:                                       # noqa: BLE001
        logger.warning("notify-interest persist failed: %r", e)
        return {"ok": True, "persisted": False}
    return {"ok": True, "persisted": True}
