"""
services/skill_usage.py — Iter 123b ORA skill telemetry.

Fire-and-forget Mongo logger. Records ONE doc per tool invocation so we
can answer the only question that matters at industry-ceiling skill
counts: "which of the 22 skills is actually carrying the product?"

Within 2 weeks of live traffic this gives us a data-driven prune list —
anything used <2% of turns is dead weight.

Schema (`ora_skill_usage` collection):
  {
    ts:          ISO8601 UTC,
    user_id:     str | None,
    project_id:  str | None,
    session_id:  str | None,
    tool:        str,
    ok:          bool,
    elapsed_ms:  int | None,
    error_kind:  str | None,   # first 80 chars of error if !ok
  }

NEVER blocks the orchestrator — every failure is swallowed with a log.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from cto_services.db import get_db

logger = logging.getLogger(__name__)


def log_skill_use(
    tool: str,
    ok: bool,
    elapsed_ms: Optional[int],
    error: Optional[str] = None,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Fire-and-forget. Schedule a Mongo insert without awaiting it."""
    try:
        db = get_db()
        if db is None:
            return
        doc = {
            "ts":         datetime.now(timezone.utc).isoformat(),
            "user_id":    user_id,
            "project_id": project_id,
            "session_id": session_id,
            "tool":       tool,
            "ok":         bool(ok),
            "elapsed_ms": elapsed_ms,
            "error_kind": (error or "")[:80] if error else None,
        }
        # Schedule on the running loop — never await.
        asyncio.create_task(_write(db, doc))
    except Exception as e:
        # NEVER let analytics crash the caller.
        logger.warning("log_skill_use scheduling failed: %r", e)


async def _write(db, doc: dict) -> None:
    try:
        await db.ora_skill_usage.insert_one(doc)
    except Exception as e:
        logger.warning("ora_skill_usage write failed: %r", e)
