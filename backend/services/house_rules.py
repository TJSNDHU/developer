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
        # Iter 212m-53 — Ask-Advisor dedicated slot. Separate prompt
        # text + LLM selector so admin can give Ask Advisor its own
        # voice/rules independent of the main chat house rules.
        # `advisor_prompt_enabled` is the kill-switch for THIS slot;
        # the legacy `enabled_advisor` toggle still controls whether
        # the combined `prompt` field above applies to Ask Advisor
        # (kept for backward compat). When both are on, both blocks
        # are injected (advisor-only first, then combined).
        "advisor_prompt":          "",
        "advisor_prompt_enabled":  False,
        "advisor_llm":             "glm-5.2",
        # Iter 212m-171 — dedicated CHAT prompt slot + model/temperature/
        # max_tokens overrides so admin can tune ORA chat independently
        # of the combined `prompt` field.  `chat_prompt_enabled` is the
        # kill-switch — when False (default), orchestrator ignores
        # chat_prompt entirely so existing behaviour stays byte-identical.
        "chat_prompt":             "",
        "chat_prompt_enabled":     False,
        "chat_model":              "",       # empty → orchestrator picks per intent
        "chat_temperature":        0.2,
        "chat_max_tokens":         4000,
        "advisor_temperature":     0.2,
        "advisor_max_tokens":      2500,
        "updated_at":       None,
        "updated_by":       None,
        "_source":          reason,
    }


# Iter 212m-53 — LLM choices for the Ask Advisor selector. Mirrors
# the actual call helpers in services/llm.py so admin can route the
# advisor to any model we already integrate. Keys are stable; labels
# / vendor / cost-tag drive the admin UI dropdown.
ADVISOR_LLM_CHOICES: list[dict] = [
    {"id": "glm-5.2",
     "label": "GLM-5.2 (OpenRouter primary — default)",
     "vendor": "openrouter", "cost": "paid"},
    {"id": "claude-sonnet-4.5",
     "label": "Claude Sonnet 4.5 (OpenRouter)",
     "vendor": "openrouter", "cost": "paid"},
    {"id": "deepseek-chat",
     "label": "DeepSeek Chat (OpenRouter)",
     "vendor": "openrouter", "cost": "paid"},
    {"id": "deepseek-direct",
     "label": "DeepSeek direct (api.deepseek.com)",
     "vendor": "deepseek", "cost": "paid"},
    {"id": "groq-llama-3.3-70b",
     "label": "Groq Llama-3.3-70B (free emergency)",
     "vendor": "groq", "cost": "free"},
]


def _valid_advisor_llm(value: str) -> str:
    valid_ids = {c["id"] for c in ADVISOR_LLM_CHOICES}
    return value if value in valid_ids else "glm-5.2"


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
    # Iter 212m-53 — Ask Advisor dedicated fields.
    advisor_prompt = (payload.get("advisor_prompt") or "").strip()
    if len(advisor_prompt) > _MAX_PROMPT_LEN:
        advisor_prompt = advisor_prompt[:_MAX_PROMPT_LEN]
    advisor_llm = _valid_advisor_llm(
        (payload.get("advisor_llm") or "glm-5.2").strip().lower()
    )
    doc = {
        "_id":              _SINGLETON_ID,
        "prompt":           prompt,
        "enabled_chat":     bool(payload.get("enabled_chat",     False)),
        "enabled_advisor":  bool(payload.get("enabled_advisor",  False)),
        "enabled_swift":    bool(payload.get("enabled_swift",    False)),
        "enabled_pro":      bool(payload.get("enabled_pro",      False)),
        "enabled_maxx":     bool(payload.get("enabled_maxx",     False)),
        "advisor_prompt":           advisor_prompt,
        "advisor_prompt_enabled":   bool(payload.get("advisor_prompt_enabled", False)),
        "advisor_llm":              advisor_llm,
        # Iter 212m-171 — dedicated chat prompt + tuning.
        "chat_prompt":              (payload.get("chat_prompt") or "").strip()[:_MAX_PROMPT_LEN],
        "chat_prompt_enabled":      bool(payload.get("chat_prompt_enabled", False)),
        "chat_model":               (payload.get("chat_model") or "").strip()[:60],
        "chat_temperature":         float(payload.get("chat_temperature") or 0.2),
        "chat_max_tokens":          int(payload.get("chat_max_tokens") or 4000),
        "advisor_temperature":      float(payload.get("advisor_temperature") or 0.2),
        "advisor_max_tokens":       int(payload.get("advisor_max_tokens") or 2500),
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



# Iter 212m-53 — Ask Advisor dedicated helpers.

async def get_active_advisor_prompt() -> str:
    """Return the Ask-Advisor-only prompt when its kill-switch is on,
    else empty string. INDEPENDENT of the legacy combined `prompt`
    field — this is the dedicated slot the admin can use to give
    Ask Advisor its own voice without polluting the main chat
    house rules."""
    doc = await get_house_rules_doc()
    if not doc.get("advisor_prompt_enabled"):
        return ""
    text = (doc.get("advisor_prompt") or "").strip()
    return text


async def get_active_advisor_llm() -> str:
    """Return the LLM slug the admin selected for Ask Advisor.
    Defaults to `glm-5.2` (current production behaviour) so an
    un-configured advisor keeps shipping identical responses."""
    doc = await get_house_rules_doc()
    return _valid_advisor_llm(doc.get("advisor_llm") or "glm-5.2")


# Iter 212m-171 — Dedicated CHAT prompt getter (mirrors advisor).
async def get_active_chat_prompt() -> str:
    """Return the admin-defined CHAT prompt when its kill-switch is on,
    else empty string.  Injected AFTER the ORA boundary rule and
    BEFORE the AUREM CTO persona so the admin can tune tone / behaviour
    without polluting either layer."""
    doc = await get_house_rules_doc()
    if not doc.get("chat_prompt_enabled"):
        return ""
    return (doc.get("chat_prompt") or "").strip()
