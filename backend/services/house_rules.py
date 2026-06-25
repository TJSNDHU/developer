"""
services/house_rules.py — Admin-defined "House Rules" prompt.

The admin panel can write a single global prompt that ORA reads FIRST,
before its own persona / tool catalog / project context — so the rules
take the highest priority. Each target (chat / advisor) and each mode
(swift / pro / maxx) has an individual green/red toggle, so the admin
can scope where the rules apply.

Storage
-------
Single MongoDB doc in collection `house_rules`, _id = "singleton":

    {
      "_id":              "singleton",
      "prompt":           str,
      "enabled_chat":     bool,   # apply to /chat/stream + /chat/send
      "enabled_advisor":  bool,   # apply to Ask Advisor (agent=ora)
      "enabled_swift":    bool,   # apply when mode=swift
      "enabled_pro":      bool,   # apply when mode=pro
      "enabled_maxx":     bool,   # apply when mode=maxx
      "updated_at":       datetime (UTC),
      "updated_by":       str (admin user_id),
    }

Defaults
--------
On first read, returns a stub doc with prompt="" and all toggles OFF
so behaviour is byte-identical to pre-feature ORA until an admin
explicitly enables it.

Public API
----------
    get_house_rules_doc()            -> dict   (raw doc for admin UI)
    set_house_rules_doc(payload,by)  -> dict   (validates + writes)
    get_active_house_rules(target,mode) -> str (prompt or "" if off)

This module is import-safe even when MongoDB is unreachable (returns
the OFF-stub) so a flaky DB never breaks chat.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from cto_services.db import get_db

logger = logging.getLogger(__name__)

_COLLECTION   = "house_rules"
_SINGLETON_ID = "singleton"
_MAX_PROMPT_LEN = 8000  # 8 KB hard cap — guards against accidental novels

# ── In-memory cache ───────────────────────────────────────────────────
# Chat traffic reads this on every turn; we don't want a Mongo round
# trip per request. TTL 30s gives admins near-real-time control while
# absorbing burst traffic. Set `_invalidate_cache()` on every write.
_CACHE: dict = {"doc": None, "fetched_at": 0.0}
_CACHE_TTL_S = 30.0


def _off_stub(reason: str = "default") -> dict:
    return {
        "_id":              _SINGLETON_ID,
        "prompt":           "",
        "enabled_chat":     False,
        "enabled_advisor":  False,
        "enabled_swift":    False,
        "enabled_pro":      False,
        "enabled_maxx":     False,
        "updated_at":       None,
        "updated_by":       None,
        "_source":          reason,
    }


def _invalidate_cache() -> None:
    _CACHE["doc"] = None
    _CACHE["fetched_at"] = 0.0


async def _read_doc_uncached() -> dict:
    db = get_db()
    if db is None:
        logger.debug("house_rules: DB unavailable — returning OFF stub")
        return _off_stub("db-unavailable")
    try:
        doc = await db[_COLLECTION].find_one({"_id": _SINGLETON_ID})
    except Exception as e:
        logger.warning("house_rules: read failed (%r) — OFF stub", e)
        return _off_stub("read-error")
    if not doc:
        return _off_stub("not-seeded")
    # Coerce missing keys to safe defaults (forward-compatible).
    base = _off_stub("loaded")
    base.update({k: v for k, v in doc.items() if v is not None})
    base["_source"] = "loaded"
    return base


async def get_house_rules_doc() -> dict:
    """Read with 30s in-process cache. Use _invalidate_cache() after writes."""
    import time as _time
    now = _time.monotonic()
    if _CACHE["doc"] is not None and (now - _CACHE["fetched_at"]) < _CACHE_TTL_S:
        return _CACHE["doc"]
    doc = await _read_doc_uncached()
    _CACHE["doc"] = doc
    _CACHE["fetched_at"] = now
    return doc


async def set_house_rules_doc(payload: dict, by_user_id: str) -> dict:
    """Validate + write. Returns the persisted doc."""
    prompt = (payload.get("prompt") or "").strip()
    if len(prompt) > _MAX_PROMPT_LEN:
        prompt = prompt[:_MAX_PROMPT_LEN]
    doc = {
        "_id":              _SINGLETON_ID,
        "prompt":           prompt,
        "enabled_chat":     bool(payload.get("enabled_chat",     False)),
        "enabled_advisor":  bool(payload.get("enabled_advisor",  False)),
        "enabled_swift":    bool(payload.get("enabled_swift",    False)),
        "enabled_pro":      bool(payload.get("enabled_pro",      False)),
        "enabled_maxx":     bool(payload.get("enabled_maxx",     False)),
        "updated_at":       datetime.now(timezone.utc),
        "updated_by":       by_user_id or "unknown",
    }
    db = get_db()
    if db is None:
        raise RuntimeError("database unavailable — house rules not saved")
    await db[_COLLECTION].update_one(
        {"_id": _SINGLETON_ID}, {"$set": doc}, upsert=True
    )
    _invalidate_cache()
    # Return a stub-shaped dict (datetime → iso) for direct JSON serialisation.
    out = {**doc, "updated_at": doc["updated_at"].isoformat(),
           "_source": "just-written"}
    return out


async def get_active_house_rules(target: str, mode: Optional[str] = None) -> str:
    """Return the prompt text if BOTH the target toggle AND the mode
    toggle (for chat target) are green. Otherwise empty string.

    target: "chat" | "advisor"
    mode  : "swift" | "pro" | "maxx" | None (advisor has no mode)
    """
    doc = await get_house_rules_doc()
    prompt = (doc.get("prompt") or "").strip()
    if not prompt:
        return ""
    t = (target or "").lower().strip()
    if t == "chat":
        if not doc.get("enabled_chat"):
            return ""
        m = (mode or "swift").lower().strip()
        if m == "swift" and not doc.get("enabled_swift"):
            return ""
        if m == "pro"   and not doc.get("enabled_pro"):
            return ""
        if m == "maxx"  and not doc.get("enabled_maxx"):
            return ""
    elif t == "advisor":
        if not doc.get("enabled_advisor"):
            return ""
    else:
        # Unknown target — don't inject.
        return ""
    return prompt


def format_house_rules_block(prompt: str) -> str:
    """Wrap the admin prompt in a header so the LLM understands these
    rules OVERRIDE every other instruction in the system prompt."""
    if not prompt or not prompt.strip():
        return ""
    return (
        "=== ADMIN HOUSE RULES (HIGHEST PRIORITY — READ FIRST) ===\n"
        "These rules are set by the Aurem CTO admin and take precedence "
        "over every other instruction in this system prompt. Follow them "
        "exactly before applying any other persona, tool, or routing "
        "guidance.\n\n"
        f"{prompt.strip()}\n"
        "=== END ADMIN HOUSE RULES ===\n"
    )
