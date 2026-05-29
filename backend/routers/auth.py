"""
routers/auth.py — AUREM Dev
Developer signup, login, token endpoints.
"""
from __future__ import annotations
import uuid
import logging
from typing import Optional

import bcrypt
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from cto_services.auth import create_token, current_dev
from cto_services.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


class SignupBody(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/signup")
async def signup(body: SignupBody) -> dict:
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    existing = await db.dev_users.find_one({"email": body.email})
    if existing:
        raise HTTPException(409, "Email already registered")
    hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    user_id = uuid.uuid4().hex
    await db.dev_users.insert_one({
        "user_id": user_id,
        "email": body.email,
        "name": body.name or body.email.split("@")[0],
        "password": hashed,
        "tier": "free",
        "tokens_remaining": 1000,
    })
    token = create_token(user_id, body.email)
    return {
        "ok": True,
        "token": token,
        "user_id": user_id,
        "email": body.email,
        "name": body.name or body.email.split("@")[0],
        "tier": "free",
        "tokens_remaining": 1000,
    }


@router.post("/login")
async def login(body: LoginBody) -> dict:
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    user = await db.dev_users.find_one({"email": body.email}, {"_id": 0})
    if not user:
        raise HTTPException(401, "Invalid credentials")
    if not bcrypt.checkpw(body.password.encode(), user["password"].encode()):
        raise HTTPException(401, "Invalid credentials")
    token = create_token(user["user_id"], user["email"], user.get("is_admin", False))
    return {
        "ok": True,
        "token": token,
        "user_id": user["user_id"],
        "email": user["email"],
        "name": user.get("name", user["email"].split("@")[0]),
        "tier": user.get("tier", "free"),
        "tokens_remaining": user.get("tokens_remaining", 0),
    }


@router.get("/me")
async def me(authorization: Optional[str] = Header(None)) -> dict:
    payload = await current_dev(authorization)
    db = get_db()
    user = None
    if db is not None:
        user = await db.dev_users.find_one(
            {"user_id": payload["user_id"]}, {"_id": 0, "password": 0}
        )
    return {"ok": True, "user": user or payload}
