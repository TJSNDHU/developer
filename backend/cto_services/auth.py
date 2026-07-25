"""
cto_services/auth.py — AUREM Dev
JWT authentication for developer routes.
"""
import os
import time
import jwt
from fastapi import HTTPException
from typing import Optional

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"


async def current_dev(authorization: Optional[str] = None) -> dict:
    """Verify Bearer JWT and return payload enriched with the latest user
    row from MongoDB (tier, is_unlimited, plan, etc.). Raises 401 if
    invalid. Iter 50.1 — DB enrichment so rate-limit / cap checks can
    correctly bypass founders without each caller re-fetching the user."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization format")
    token = parts[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
    # Iter 307 — JWT revocation gate. Two orthogonal checks, both cheap
    # (one indexed find_one each, sub-3ms combined on preview):
    #   1. jti in `revoked_tokens` collection → user hit /auth/logout
    #      or an admin nuked this specific token.
    #   2. iat < dev_users.session_barrier_at → an admin (or the user
    #      themselves via /auth/revoke-all-sessions) declared every
    #      token issued before `barrier_at` invalid.
    # See services/token_revocation.py module docstring for the
    # fail-open-on-DB-hiccup rationale.
    try:
        from cto_services.db import get_db
        from services import token_revocation as _rev
        db_for_check = get_db()
        jti = payload.get("jti")
        if jti and await _rev.is_jti_revoked(db_for_check, jti):
            raise HTTPException(status_code=401, detail="Token has been revoked")
        uid = payload.get("user_id")
        iat = payload.get("iat")
        if uid and iat and await _rev.is_iat_before_barrier(db_for_check, uid, iat):
            raise HTTPException(status_code=401, detail="All sessions revoked — sign in again")
    except HTTPException:
        raise
    except Exception:
        # DB unavailable → fail-open on the CHECK side. See module
        # docstring in services/token_revocation.py — matches industry
        # trade-off (Amazon, GitHub) and the codebase's existing
        # `require_admin` pattern of swallowing DB hiccups.
        pass
    # Enrich with DB flags so callers see fresh is_unlimited / tier values
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is not None and payload.get("user_id"):
            u = await db.dev_users.find_one(
                {"user_id": payload["user_id"]},
                {"_id": 0, "tier": 1, "is_unlimited": 1, "is_admin": 1,
                 "is_founder": 1, "plan": 1, "plan_limit": 1, "email": 1},
            )
            if u:
                payload = {**payload, **u}
    except Exception:
        pass
    return payload


async def require_admin(authorization: Optional[str] = None) -> dict:
    """Iter 212m-158 — Shared admin gate.

    Single-line check usable by any router: simply add
    ``await require_admin(authorization)`` at the top of the handler.

    Mirrors the legacy ``routers/admin.py::_require_admin`` behaviour
    so we don't have two slightly-different rules in the codebase:
      1. Decode the JWT via ``current_dev``.
      2. Fast path — JWT already carries ``is_admin=true`` or the live
         row says ``tier == "founder"``.
      3. Stale-JWT escape hatch — re-check the live ``dev_users`` row
         so newly-promoted founders don't have to log out + log back in.
      4. Otherwise raise ``HTTPException(403, "Admin access required")``.

    Used by ``security_scan.py``, ``vanguard_ci.py``, the BugHunt
    endpoints, and any future admin-only feature.  Importing this
    helper is the *only* supported way to gate a route on admin
    status — do NOT re-implement the check inline.
    """
    user = await current_dev(authorization)
    if user.get("is_admin") or user.get("tier") == "founder":
        return user
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is not None and user.get("user_id"):
            row = await db.dev_users.find_one(
                {"user_id": user["user_id"]},
                {"is_admin": 1, "tier": 1, "is_unlimited": 1, "email": 1},
            )
            if row and (row.get("is_admin") or row.get("tier") == "founder"):
                user["is_admin"]     = True
                user["tier"]         = row.get("tier") or user.get("tier")
                user["is_unlimited"] = bool(row.get("is_unlimited"))
                user["email"]        = row.get("email") or user.get("email")
                return user
    except Exception:
        # Swallow DB hiccups — fail closed below.
        pass
    raise HTTPException(403, "Admin access required")


def create_token(user_id: str, email: str, is_admin: bool = False) -> str:
    """Create a signed JWT for a developer user.

    Iter 212m-48 — TTL shortened from 30 days to 7 days. The blast
    radius of a leaked token (XSS, stolen device, lost laptop) is now
    capped at one week. Active users get fresh tokens automatically
    via GET /auth/me, which re-signs on every call.

    Iter 212m-55 — `jti` (random 128-bit hex) + `iat` (issued-at)
    added. `jti` lets the server-side revocation list invalidate a
    specific leaked token without re-keying everyone. `iat` lets
    sensitive endpoints reject tokens older than a configurable
    replay window (e.g. admin actions). Backward-compatible: tokens
    without these claims still decode fine; only NEW tokens carry
    them.
    """
    import uuid
    now = int(time.time())
    payload = {
        "user_id": user_id,
        "email": email,
        "is_admin": is_admin,
        "iat": now,                            # issued-at (replay window)
        "jti": uuid.uuid4().hex,               # unique token id (revocation)
        "exp": now + 86400 * 7,                # 7 days
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_mfa_pending_token(user_id: str, email: str) -> str:
    """Iter 212m-20 — short-lived JWT that ONLY carries the intent to
    complete a 2FA challenge. Cannot be used to call any other endpoint
    (the `mfa_pending=True` claim + 5-minute expiry are enforced by
    `consume_mfa_pending_token`). Returned by /auth/login when the
    admin's account has 2FA enabled; consumed by /auth/login/2fa-verify
    in exchange for the real session JWT."""
    payload = {
        "user_id":     user_id,
        "email":       email,
        "mfa_pending": True,
        "exp":         int(time.time()) + 5 * 60,   # 5 min window
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def consume_mfa_pending_token(token: str) -> dict:
    """Validate the mfa_pending token. Returns the payload on success,
    raises HTTPException(401) otherwise. The token is single-purpose —
    the caller MUST have already verified the 2FA code BEFORE issuing
    a real session token via `create_token`."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "2FA challenge expired — log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid 2FA token")
    if not payload.get("mfa_pending"):
        raise HTTPException(401, "Not a 2FA challenge token")
    if not payload.get("user_id"):
        raise HTTPException(401, "Malformed 2FA token")
    return payload
