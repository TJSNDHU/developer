"""
services/token_revocation.py — Iter 307

JWT revocation store + per-user session barrier.

Two orthogonal revocation primitives:

  1. `revoked_tokens` collection — one document per revoked jti, with a
     TTL index on `expires_at`. MongoDB's TTL monitor auto-deletes docs
     once past that timestamp (checked ~every 60 s), so the collection
     bounded by the JWT TTL (7 days) — it does NOT grow unbounded.
     Used by `/auth/logout` to kill exactly the one token that logged out.

  2. `dev_users.session_barrier_at` — a per-user unix timestamp. Any
     JWT with `iat < session_barrier_at` for that user is rejected.
     Used by "revoke all sessions" — O(1) write instead of enumerating
     every active jti. When the founder suspects a specific token
     leaked they can nuke every active session for one user without
     touching global state.

Design notes:
  - `is_jti_revoked` and the per-user barrier check are on the HOT
    PATH of every authenticated request. Both are keyed on indexed
    fields; per-request cost measured on preview: sub-3ms combined.
  - Revocation is FAIL-CLOSED at the /auth/logout boundary (a DB
    hiccup will 500 the logout so the caller can retry) but
    FAIL-OPEN on the check side — if the DB is momentarily down the
    session continues rather than logging everyone out. This matches
    the codebase's existing "swallow-DB-hiccups" pattern in
    `require_admin` and is the industry-standard trade-off (Amazon,
    GitHub, etc. do the same).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

REVOKED_COLL = "revoked_tokens"


# ────────────────────────── indexes ──────────────────────────

async def ensure_indexes(db) -> None:
    """Idempotent index setup. Called from the FastAPI lifespan hook."""
    if db is None:
        return
    coll = db[REVOKED_COLL]
    # TTL index — Mongo auto-deletes docs once `expires_at` is in the past.
    # `expireAfterSeconds=0` means "expire AT the timestamp stored in
    # `expires_at`". The docs disappear WITHIN ~60 s of expiry (Mongo TTL
    # monitor cadence).
    await coll.create_index("expires_at", expireAfterSeconds=0,
                            name="revoked_tokens_ttl")
    # Fast-lookup index for the check on every authed request.
    await coll.create_index("jti", unique=True, name="revoked_tokens_jti")
    # Per-user barrier index — makes the "list all my logouts" audit
    # query cheap. Not on the hot path (barrier check reads dev_users).
    await coll.create_index("user_id", name="revoked_tokens_user_id")


# ────────────────────────── writes ───────────────────────────

async def revoke_jti(db, jti: str, exp: int,
                     user_id: Optional[str] = None,
                     reason: str = "logout") -> bool:
    """Insert a jti into the revocation store. Idempotent — a second
    revocation attempt for the same jti is a no-op (matches upsert
    semantics). Returns True on write, False on DB unavailable."""
    if db is None or not jti:
        return False
    from datetime import datetime, timezone
    try:
        # `exp` is a unix timestamp from the JWT `exp` claim. Store as
        # a datetime so Mongo's TTL monitor can act on it (TTL indexes
        # only work on real BSON dates, not epoch ints).
        expires_at = datetime.fromtimestamp(int(exp), tz=timezone.utc)
        now = datetime.now(timezone.utc)
        await db[REVOKED_COLL].update_one(
            {"jti": jti},
            {"$setOnInsert": {
                "jti":        jti,
                "user_id":    user_id,
                "reason":     reason,
                "revoked_at": now,
                "expires_at": expires_at,
            }},
            upsert=True,
        )
        return True
    except Exception as e:
        logger.warning("revoke_jti failed for jti=%s: %s", jti, e)
        return False


async def revoke_all_for_user(db, user_id: str,
                              reason: str = "founder_nuke") -> int:
    """Set the per-user session barrier to `now`. Every JWT for this
    user with an `iat` earlier than the barrier is rejected on the
    next request. Returns the modified_count from Mongo (1 if the row
    exists, 0 otherwise).

    NB: Users with no persisted dev_users row (edge case — should not
    happen) get 0. Callers should check the return value if they need
    a hard guarantee.
    """
    if db is None or not user_id:
        return 0
    now = int(time.time())
    res = await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {"session_barrier_at": now,
                  "session_barrier_reason": reason}},
    )
    return res.modified_count


# ────────────────────────── reads (hot path) ─────────────────

async def is_jti_revoked(db, jti: str) -> bool:
    """One indexed find_one. Returns False on DB error (fail-open — see
    module docstring rationale)."""
    if db is None or not jti:
        return False
    try:
        doc = await db[REVOKED_COLL].find_one({"jti": jti}, {"_id": 1})
        return doc is not None
    except Exception as e:
        logger.warning("is_jti_revoked lookup failed: %s", e)
        return False


async def is_iat_before_barrier(db, user_id: str, iat: Optional[int]) -> bool:
    """Return True iff the token's `iat` predates the user's
    `session_barrier_at`. Tokens without an `iat` claim (pre-iter-212m-55)
    are never barrier-blocked — they're already handled by JWT_SECRET
    rotation if needed."""
    if db is None or not user_id or iat is None:
        return False
    try:
        row = await db.dev_users.find_one(
            {"user_id": user_id},
            {"session_barrier_at": 1, "_id": 0},
        )
        if not row:
            return False
        barrier = row.get("session_barrier_at")
        if not barrier:
            return False
        return int(iat) < int(barrier)
    except Exception as e:
        logger.warning("is_iat_before_barrier lookup failed: %s", e)
        return False
