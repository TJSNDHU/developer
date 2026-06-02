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
