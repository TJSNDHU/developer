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
    """Verify Bearer JWT and return payload. Raises 401 if invalid."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization format")
    token = parts[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def create_token(user_id: str, email: str, is_admin: bool = False) -> str:
    """Create a signed JWT for a developer user."""
    payload = {
        "user_id": user_id,
        "email": email,
        "is_admin": is_admin,
        "exp": int(time.time()) + 86400 * 30,  # 30 days
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
