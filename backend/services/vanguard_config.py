"""
services/vanguard_config.py — per-mode Vanguard verify settings.

Storage: single Mongo doc in `app_config`:

    {
      _id: "vanguard",
      enabled: bool,                                     # master switch
      levels:  { swift: "CRITICAL" | "HIGH" | "OFF", ... },
      updated_at: datetime,
      updated_by: user_id,
    }

Reads are best-effort and self-healing: when the doc is missing we
fall back to the env-var defaults compiled at import time so the
verify pipeline never gates on infra hiccups.  A tiny in-memory TTL
cache (~10 s) keeps the per-commit overhead at one Mongo lookup per
batch of ships rather than one per file.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Defaults (used when no Mongo doc exists yet) ─────────────────────
_DEFAULT_LEVEL = (
    os.environ.get("VANGUARD_VERIFY_BLOCK_LEVEL", "CRITICAL") or "CRITICAL"
).upper()
_DEFAULT_ENABLED = (
    os.environ.get("VANGUARD_VERIFY_ENABLED", "1").lower()
    in ("1", "true", "yes", "on")
)
_VALID_LEVELS = ("OFF", "CRITICAL", "HIGH")
_VALID_MODES  = ("swift", "pro", "maxx")
_DOC_ID       = "vanguard"

# 10-second cache so a burst of 50 file edits inside one commit
# doesn't hit Mongo 50 times for the same setting.
_CACHE_TTL_S  = 10.0
_cache: dict = {"ts": 0.0, "doc": None}


def default_config() -> dict:
    """Hard-coded fallback used when there's no DB doc yet."""
    return {
        "enabled":    _DEFAULT_ENABLED,
        "levels":     {m: _DEFAULT_LEVEL for m in _VALID_MODES},
        "updated_at": None,
        "updated_by": None,
    }


async def _load_doc() -> dict:
    """Fetch the singleton with TTL caching. Never raises."""
    now = time.monotonic()
    if _cache["doc"] is not None and (now - _cache["ts"]) < _CACHE_TTL_S:
        return _cache["doc"]
    from cto_services.db import get_db
    db = get_db()
    doc: Optional[dict] = None
    if db is not None:
        try:
            doc = await db.app_config.find_one({"_id": _DOC_ID})
        except Exception as e:
            logger.warning("vanguard_config load failed: %r", e)
    if not doc:
        doc = default_config()
    else:
        # Normalise — keep only the keys we own + valid levels.
        levels_in = (doc.get("levels") or {})
        levels    = {}
        for m in _VALID_MODES:
            v = (levels_in.get(m) or _DEFAULT_LEVEL).upper()
            levels[m] = v if v in _VALID_LEVELS else _DEFAULT_LEVEL
        doc = {
            "enabled":    bool(doc.get("enabled", _DEFAULT_ENABLED)),
            "levels":     levels,
            "updated_at": doc.get("updated_at"),
            "updated_by": doc.get("updated_by"),
        }
    _cache["doc"] = doc
    _cache["ts"]  = now
    return doc


async def get_config() -> dict:
    """Public read — returns the full config dict."""
    return await _load_doc()


async def get_mode_settings(mode: str) -> tuple[bool, str]:
    """Resolve `(enabled, block_level)` for a given Swift/Pro/Maxx mode.
    Unknown modes default to the strictest level so a typo never
    accidentally weakens enforcement."""
    cfg = await _load_doc()
    if not cfg.get("enabled", True):
        return False, "OFF"
    level = (cfg["levels"].get((mode or "").lower())
             or _DEFAULT_LEVEL).upper()
    if level not in _VALID_LEVELS:
        level = _DEFAULT_LEVEL
    return True, level


async def save_config(
    *,
    enabled: bool,
    levels:  dict,
    updated_by: Optional[str] = None,
) -> dict:
    """Upsert the singleton. Returns the normalised stored value."""
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise RuntimeError("database unavailable")

    safe_levels: dict[str, str] = {}
    for m in _VALID_MODES:
        v = (levels.get(m) or _DEFAULT_LEVEL).upper()
        if v not in _VALID_LEVELS:
            v = _DEFAULT_LEVEL
        safe_levels[m] = v

    doc = {
        "_id":        _DOC_ID,
        "enabled":    bool(enabled),
        "levels":     safe_levels,
        "updated_at": datetime.now(timezone.utc),
        "updated_by": updated_by,
    }
    await db.app_config.update_one(
        {"_id": _DOC_ID}, {"$set": doc}, upsert=True,
    )
    # Bust the cache so the next verify call sees the change instantly.
    _cache["doc"] = None
    _cache["ts"]  = 0.0
    doc.pop("_id", None)
    return doc
