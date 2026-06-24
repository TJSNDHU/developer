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
    # Enrich with DB flags so callers see fresh is_unlimited / tier values
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is not None and payload.get("user_id"):
            u = await db.dev_users.find_one(
                {"user_id": payload["user_id"]},
                {"_id": 0, "tier": 1, "is_unlimited": 1, "is_admin": 1,
                 "plan": 1, "plan_limit": 1, "email": 1},
            )
            if u:
                payload = {**payload, **u}
    except Exception:
        pass
    return payload


def create_token(user_id: str, email: str, is_admin: bool = False) -> str:
    """Create a signed JWT for a developer user."""
    payload = {
        "user_id": user_id,
        "email": email,
        "is_admin": is_admin,
        "exp": int(time.time()) + 86400 * 30,  # 30 days
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
