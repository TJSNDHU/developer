"""
services/ora_chat/house_rules.py — Iter 212m-239

Admin-editable behavior rules for ORA Chat. Layered strictly on top
of the immutable CORE_SAFETY_RULES + AUREM_CONTEXT (see safety.py).

Storage: `ora_chat_house_rules` Mongo collection. One "current" row
per admin_user_id. Every update writes a new version doc (not an
in-place replace) so the last 5 versions are retained for rollback.

Public surface:
    get_current(user_id)           → dict | None
    update(user_id, text)          → new-version dict + soft_warning
    list_history(user_id, n=5)     → list[dict]  (newest first)
    restore(user_id, version)      → new version cloned from target
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from cto_services.db import get_db
from services.ora_chat.safety import (
    DEFAULT_HOUSE_RULES, house_rules_soft_warning,
)


MAX_LEN = 4000
HISTORY_KEEP = 5


async def get_current(user_id: str) -> Optional[dict]:
    """Return the current active house rules for a user, or None."""
    db = get_db()
    if db is None:
        return None
    row = await db.ora_chat_house_rules.find_one(
        {"admin_user_id": user_id, "active": True},
        {"_id": 0},
    )
    return row


async def get_effective_text(user_id: str) -> str:
    """The text to layer into the system prompt. Falls back to the
    hard-coded DEFAULT_HOUSE_RULES if the admin has never saved one."""
    row = await get_current(user_id)
    if row and row.get("rules_text"):
        return row["rules_text"]
    return DEFAULT_HOUSE_RULES


async def _next_version(user_id: str) -> int:
    db = get_db()
    if db is None:
        return 1
    top = await db.ora_chat_house_rules.find_one(
        {"admin_user_id": user_id},
        {"version": 1, "_id": 0},
        sort=[("version", -1)],
    )
    return int((top or {}).get("version", 0)) + 1


async def _prune_old_versions(user_id: str) -> int:
    """Keep the newest HISTORY_KEEP versions; hard-delete older ones."""
    db = get_db()
    if db is None:
        return 0
    keep_cursor = db.ora_chat_house_rules.find(
        {"admin_user_id": user_id},
        {"version": 1, "_id": 0},
    ).sort("version", -1).limit(HISTORY_KEEP)
    keep_versions = [d["version"] async for d in keep_cursor]
    if not keep_versions:
        return 0
    r = await db.ora_chat_house_rules.delete_many({
        "admin_user_id": user_id,
        "version": {"$lt": min(keep_versions)},
    })
    return r.deleted_count


async def update(user_id: str, text: str) -> dict:
    """Persist a new version. Raises ValueError on length overflow."""
    if text is None:
        text = ""
    text = text.strip()
    if len(text) > MAX_LEN:
        raise ValueError(f"house_rules text too long ({len(text)} > {MAX_LEN})")
    db = get_db()
    if db is None:
        raise RuntimeError("database_unavailable")

    version = await _next_version(user_id)
    now = time.time()
    # Deactivate previous "current" (still kept in history, just not active).
    await db.ora_chat_house_rules.update_many(
        {"admin_user_id": user_id, "active": True},
        {"$set": {"active": False}},
    )
    doc = {
        "id":             uuid.uuid4().hex,
        "admin_user_id":  user_id,
        "rules_text":     text,
        "version":        version,
        "active":         True,
        "updated_at":     now,
        "created_at":     now,
    }
    await db.ora_chat_house_rules.insert_one(doc)
    await _prune_old_versions(user_id)
    doc.pop("_id", None)
    return {
        "ok":            True,
        "rules":         doc,
        "soft_warning":  house_rules_soft_warning(text),
    }


async def list_history(user_id: str, n: int = HISTORY_KEEP) -> list[dict]:
    db = get_db()
    if db is None:
        return []
    cursor = db.ora_chat_house_rules.find(
        {"admin_user_id": user_id},
        {"_id": 0, "id": 1, "rules_text": 1, "version": 1,
         "active": 1, "updated_at": 1, "created_at": 1},
    ).sort("version", -1).limit(n)
    return [row async for row in cursor]


async def restore(user_id: str, version: int) -> dict:
    """Clone the target historical version into a new active version.

    Never destroys history — the restore itself is captured as a new
    row so the audit trail stays honest.
    """
    db = get_db()
    if db is None:
        raise RuntimeError("database_unavailable")
    src = await db.ora_chat_house_rules.find_one(
        {"admin_user_id": user_id, "version": int(version)},
        {"_id": 0, "rules_text": 1},
    )
    if not src:
        raise ValueError(f"version {version} not found")
    return await update(user_id, src.get("rules_text", ""))


async def reset_to_default(user_id: str) -> dict:
    """UI 'Reset to default' — writes DEFAULT_HOUSE_RULES as a new
    version (does not delete history)."""
    return await update(user_id, DEFAULT_HOUSE_RULES)
