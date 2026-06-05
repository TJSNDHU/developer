"""
routers/auth.py — AUREM Dev
Developer signup, login, token endpoints.
"""
from __future__ import annotations
import uuid
import os
import logging
from typing import Optional

import bcrypt
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from cto_services.auth import create_token, current_dev
from cto_services.db import get_db
from services.usage import is_founder_email

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
    # Founder allow-list: anyone on the list signs up directly into the
    # `founder` tier with admin rights so first-time onboarding doesn't
    # require a manual DB update.
    is_founder = is_founder_email(body.email)
    tier = "founder" if is_founder else "free"
    starting_tokens = 10**9 if is_founder else 1000
    await db.dev_users.insert_one({
        "user_id": user_id,
        "email": body.email,
        "name": body.name or body.email.split("@")[0],
        "password": hashed,
        "tier": tier,
        "tokens_remaining": starting_tokens,
        "is_admin": is_founder,
        "is_unlimited": is_founder,
    })
    token = create_token(user_id, body.email, is_admin=is_founder)
    return {
        "ok": True,
        "token": token,
        "user_id": user_id,
        "email": body.email,
        "name": body.name or body.email.split("@")[0],
        "tier": tier,
        "tokens_remaining": starting_tokens,
        "is_admin": is_founder,
        "is_unlimited": is_founder,
    }


@router.post("/login")
async def login(body: LoginBody) -> dict:
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    user = await db.dev_users.find_one({"email": body.email}, {"_id": 0})
    if not user:
        raise HTTPException(401, "Invalid credentials")
    # OAuth-only accounts have no password — block password sign-in for
    # them with a clear message so they go through the GitHub button.
    if not user.get("password"):
        raise HTTPException(
            401,
            "This account uses GitHub sign-in. Use 'Continue with GitHub'.",
        )
    if not bcrypt.checkpw(body.password.encode(), user["password"].encode()):
        raise HTTPException(401, "Invalid credentials")
    # Auto-promote whoever matches ADMIN_EMAIL env var (cheap, idempotent)
    admin_email = os.environ.get("ADMIN_EMAIL", "").lower().strip()
    # Founder allow-list takes precedence over admin email — founder implies
    # admin + unlimited tokens. Idempotent: writes only when the DB row is
    # missing the founder bits.
    is_founder = is_founder_email(user["email"])
    is_admin = bool(user.get("is_admin")) or is_founder or (
        admin_email and user["email"].lower() == admin_email
    )
    promote: dict = {}
    if is_admin and not user.get("is_admin"):
        promote["is_admin"] = True
    if is_founder:
        if user.get("tier") != "founder":
            promote["tier"] = "founder"
        if not user.get("is_unlimited"):
            promote["is_unlimited"] = True
    if promote:
        await db.dev_users.update_one(
            {"user_id": user["user_id"]}, {"$set": promote},
        )
        user.update(promote)
    token = create_token(user["user_id"], user["email"], is_admin)
    return {
        "ok": True,
        "token": token,
        "user_id": user["user_id"],
        "email": user["email"],
        "name": user.get("name", user["email"].split("@")[0]),
        "tier": user.get("tier", "free"),
        "tokens_remaining": user.get("tokens_remaining", 0),
        "is_admin": is_admin,
        "is_unlimited": bool(user.get("is_unlimited") or is_founder),
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


@router.get("/tokens")
async def get_tokens(authorization: Optional[str] = Header(None)) -> dict:
    """Return the current wallet balance for the authenticated user."""
    payload = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True, "tokens_remaining": 0}
    u = await db.dev_users.find_one(
        {"user_id": payload["user_id"]}, {"_id": 0, "tokens_remaining": 1}
    )
    return {"ok": True, "tokens_remaining": int((u or {}).get("tokens_remaining", 0))}
