"""
services/ora_chat/prompt_snapshot.py — Iter 264 Fix C

Persist the EXACT assembled system prompt for every assistant turn so
"what did the model see" is always answerable. Snapshots live in
`ora_prompt_snapshots` with a 30-day TTL; the message doc only stores
`prompt_sha256` + `component_sizes` inline (small).
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from cto_services.db import get_db

logger = logging.getLogger(__name__)

_TTL_DAYS = int(os.getenv("ORA_PROMPT_SNAPSHOT_TTL_DAYS", "30"))
_MAX_PROMPT_CHARS = 120_000
_index_ready = False


def sha256_of(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


async def _ensure_ttl_index(db) -> None:
    global _index_ready
    if _index_ready:
        return
    try:
        await db.ora_prompt_snapshots.create_index(
            "expires_at", expireAfterSeconds=0)
        _index_ready = True
    except Exception as e:                                   # noqa: BLE001
        logger.warning("prompt_snapshot TTL index failed: %r", e)


async def save_snapshot(*, message_id: str, session_id: str,
                        full_prompt: str,
                        component_sizes: Optional[dict] = None) -> dict:
    """Write one snapshot row. Never raises — returns
    {"sha256": ..., "component_sizes": ...} for the caller to inline
    on the message doc regardless of write success."""
    sha = sha256_of(full_prompt)
    sizes = component_sizes or {}
    out = {"sha256": sha, "component_sizes": sizes}
    try:
        db = get_db()
    except Exception:
        return out
    if db is None:
        return out
    try:
        await _ensure_ttl_index(db)
        now = datetime.now(timezone.utc)
        await db.ora_prompt_snapshots.insert_one({
            "message_id":      message_id,
            "session_id":      session_id,
            "sha256":          sha,
            "full_prompt":     (full_prompt or "")[:_MAX_PROMPT_CHARS],
            "component_sizes": sizes,
            "created_at":      now.isoformat(),
            "expires_at":      now + timedelta(days=_TTL_DAYS),
        })
    except Exception as e:                                   # noqa: BLE001
        logger.warning("prompt snapshot write failed: %r", e)
    return out
