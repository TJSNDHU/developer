"""
backend/scripts/init_prod_collections.py
========================================
Iter 116 — Idempotent collection bootstrap.

Why this exists:
  Production Atlas deploys to a fresh DB. Code paths that only WRITE
  to a collection (e.g. cto_payments, vanguard_audit) defer collection
  creation until the first write — meaning admin pages that READ those
  collections show empty / 404 / aggregate errors on a brand-new prod.

  This script touches every required collection so MongoDB materialises
  it, then creates the indexes the codebase depends on. Safe to run on
  every boot (lifespan startup) — every operation is idempotent.

Public API:
    await init_prod_collections(db) -> dict
        Returns {"created": [...], "indexed": [...], "errors": [...]}
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

logger = logging.getLogger(__name__)

# Each tuple = (collection_name, [(keys_spec, options_dict), ...])
# keys_spec: list of (field, direction) tuples — same shape Motor expects
# options: passed verbatim to create_index (e.g. unique=True, expireAfterSeconds)
_BOOTSTRAP_SPEC: list[tuple[str, list[tuple[list, dict]]]] = [
    ("cto_payments", [
        ([("user_id", 1), ("created_at", -1)], {}),
        ([("stripe_session_id", 1)],           {"sparse": True}),
        ([("status", 1)],                       {}),
    ]),
    ("cto_support", [
        ([("user_id", 1), ("created_at", -1)], {}),
        ([("status", 1)],                       {}),
        ([("ticket_id", 1)],                    {"unique": True, "sparse": True}),
    ]),
    ("cto_support_messages", [
        ([("ticket_id", 1), ("ts", 1)], {}),
        ([("user_id", 1)],              {}),
    ]),
    ("cto_token_grants", [
        ([("user_id", 1), ("granted_at", -1)], {}),
        ([("source", 1)],                       {}),
    ]),
    ("cto_vault_audit_log", [
        ([("user_id", 1), ("ts", -1)], {}),
        ([("action", 1)],              {}),
        ([("project_id", 1)],          {"sparse": True}),
    ]),
    ("referrals", [
        ([("referrer_id", 1)],                   {}),
        ([("referred_user_id", 1)],              {"sparse": True}),
        ([("status", 1)],                        {}),
        ([("created_at", -1)],                   {}),
    ]),
    ("vanguard_audit", [
        ([("ts_unix", -1)],          {}),
        ([("user_id", 1)],            {}),
        ([("project", 1)],            {}),
        ([("rule_triggered", 1)],     {}),
    ]),
    ("cto_automations", [
        ([("user_id", 1), ("created_at", -1)], {}),
        ([("status", 1)],                       {}),
        ([("automation_id", 1)],                {"unique": True, "sparse": True}),
    ]),
    ("project_plans", [
        ([("project_id", 1), ("created_at", -1)], {}),
        ([("user_id", 1)],                         {}),
        ([("status", 1)],                          {}),
    ]),
    ("aurem_cto_unlock_requests", [
        ([("user_id", 1), ("ts", -1)], {}),
        ([("status", 1)],              {}),
        ([("email", 1)],               {"sparse": True}),
    ]),
    # Iter 121 — auditor found these large collections running on _id
    # only. chat_sessions (801 docs) is read on every chat-history load
    # via {user_id, updated_at}; without an index that's a full scan.
    # dev_users (297 docs) is the auth + admin search target; email
    # lookup is hot path.
    ("chat_sessions", [
        ([("user_id", 1), ("updated_at", -1)], {}),
        # NB: session_id is NOT made unique — historic data has at least
        # one legitimate test duplicate ("e2e-F12") and unique enforcement
        # would block new writes. Lookups by session_id still benefit
        # from the implicit _id-prefix.
    ]),
    ("dev_users", [
        ([("email", 1)],        {"unique": True, "sparse": True}),
        ([("user_id", 1)],      {"unique": True, "sparse": True}),
        ([("created_at", -1)],  {}),
    ]),
    ("cto_tasks", [
        ([("user_id", 1), ("created_at", -1)], {}),
        ([("project_id", 1), ("created_at", -1)], {}),
        ([("task_id", 1)],     {"unique": True, "sparse": True}),
        ([("status", 1)],      {}),
    ]),
    ("cto_projects", [
        ([("user_id", 1), ("created_at", -1)], {}),
        ([("project_id", 1)],  {"unique": True, "sparse": True}),
    ]),
]

# Bootstrap sentinel — written then removed so collection materialises.
_SENTINEL_KEY  = "_init_prod_sentinel"
_SENTINEL_VAL  = "iter116-bootstrap"


async def _materialise(db, name: str) -> bool:
    """Force-create a collection by writing + deleting a sentinel doc.
    Returns True if we actually touched it (created or re-touched)."""
    try:
        existing = await db.list_collection_names()
        if name in existing:
            return False  # already there
        await db[name].insert_one({_SENTINEL_KEY: _SENTINEL_VAL,
                                    "ts": time.time()})
        await db[name].delete_one({_SENTINEL_KEY: _SENTINEL_VAL})
        return True
    except Exception as e:
        logger.warning("materialise %s failed: %r", name, e)
        return False


async def _ensure_indexes(db, name: str,
                          specs: Iterable[tuple[list, dict]]) -> int:
    n = 0
    for keys, opts in specs:
        try:
            await db[name].create_index(keys, **(opts or {}))
            n += 1
        except Exception as e:
            logger.warning("create_index %s %r failed: %r", name, keys, e)
    return n


_LAST_BOOTSTRAP: dict | None = None


def get_last_bootstrap() -> dict | None:
    """Return the result of the most recent init_prod_collections call,
    or None if it hasn't been called yet this process."""
    return _LAST_BOOTSTRAP


async def init_prod_collections(db) -> dict:
    """Idempotent bootstrap. Safe to call on every boot."""
    from datetime import datetime, timezone
    out = {"created": [], "indexed": [], "errors": [], "ts": datetime.now(timezone.utc).isoformat()}
    if db is None:
        out["errors"].append("db is None")
        global _LAST_BOOTSTRAP
        _LAST_BOOTSTRAP = out
        return out
    for name, idx_specs in _BOOTSTRAP_SPEC:
        try:
            if await _materialise(db, name):
                out["created"].append(name)
            count = await _ensure_indexes(db, name, idx_specs)
            if count:
                out["indexed"].append(f"{name}:{count}")
        except Exception as e:
            out["errors"].append(f"{name}: {type(e).__name__}: {e}")
            logger.warning("init_prod_collections %s failed: %r", name, e)
    logger.info("init_prod_collections done — created=%d, indexed=%d, errors=%d",
                len(out["created"]), len(out["indexed"]), len(out["errors"]))
    _LAST_BOOTSTRAP = out
    return out
