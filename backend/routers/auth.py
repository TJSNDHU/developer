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

from cto_services.auth import (
    create_token, current_dev, create_mfa_pending_token,
    consume_mfa_pending_token,
)
from cto_services.db import get_db
from services.usage import is_founder_email
from services.mfa import verify_code, consume_backup_code

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


class SignupBody(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class LoginBody(BaseModel):
    email: str
    password: str


class TwoFAVerifyBody(BaseModel):
    """Iter 212m-20 — payload for /auth/login/2fa-verify. Exactly one
    of `code` / `backup_code` must be supplied."""
    mfa_token: str
    code: Optional[str] = None
    backup_code: Optional[str] = None


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
    # Auto-promote whoever matches ADMIN_EMAIL or ADMIN_EMAILS env var
    # (cheap, idempotent). ADMIN_EMAIL kept for backward compat — single
    # address. Iter 181 — ADMIN_EMAILS added as a comma-separated list so
    # we can grant multiple QA / staff accounts admin without rotating
    # the legacy var and breaking existing setups.
    admin_email  = os.environ.get("ADMIN_EMAIL", "").lower().strip()
    admin_emails = {
        e.strip().lower()
        for e in os.environ.get("ADMIN_EMAILS", "").split(",")
        if e.strip()
    }
    user_email_lc = user["email"].lower()
    # Founder allow-list takes precedence over admin email — founder implies
    # admin + unlimited tokens. Idempotent: writes only when the DB row is
    # missing the founder bits.
    is_founder = is_founder_email(user["email"])
    is_admin = bool(user.get("is_admin")) or is_founder or (
        admin_email and user_email_lc == admin_email
    ) or (user_email_lc in admin_emails)
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

    # Iter 212m-20 — Admin 2FA gate.
    # Any admin account with `mfa_enabled=True` cannot complete login
    # in a single round-trip. Instead we issue a short-lived
    # `mfa_pending` token; the client must call /auth/login/2fa-verify
    # with the 6-digit TOTP code (or a backup code) to swap it for a
    # real session JWT. Non-admin accounts are unaffected.
    if is_admin and user.get("mfa_enabled") and user.get("mfa_secret"):
        mfa_token = create_mfa_pending_token(user["user_id"], user["email"])
        return {
            "ok":           True,
            "mfa_required": True,
            "mfa_token":    mfa_token,
            "email":        user["email"],
        }

    return _issue_session(user, is_admin, is_founder)


def _issue_session(user: dict, is_admin: bool, is_founder: bool) -> dict:
    """Iter 212m-20 — shared response builder used by both the
    single-step /auth/login (non-2FA path) and the two-step
    /auth/login/2fa-verify endpoints."""
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


@router.post("/login/2fa-verify")
async def login_2fa_verify(body: TwoFAVerifyBody) -> dict:
    """Iter 212m-20 — second leg of the admin 2FA login flow. Trades a
    `mfa_pending` token + 6-digit TOTP code (or a backup code) for the
    real session JWT.

    Idempotent on backup codes: a successfully consumed code is removed
    from the user's `mfa_backup_codes` array so it can never be reused.
    """
    if not body.code and not body.backup_code:
        raise HTTPException(400, "Provide either `code` or `backup_code`")
    payload = consume_mfa_pending_token(body.mfa_token)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    user = await db.dev_users.find_one(
        {"user_id": payload["user_id"]}, {"_id": 0},
    )
    if not user:
        raise HTTPException(401, "User not found")
    if not user.get("mfa_enabled") or not user.get("mfa_secret"):
        # The user disabled 2FA between leg 1 and leg 2 — treat the
        # mfa_token as a normal session promotion.
        is_founder = is_founder_email(user["email"])
        is_admin = bool(user.get("is_admin")) or is_founder
        return _issue_session(user, is_admin, is_founder)

    ok = False
    if body.code:
        ok = verify_code(user["mfa_secret"], body.code)
    elif body.backup_code:
        hashes = list(user.get("mfa_backup_codes") or [])
        ok, remaining = consume_backup_code(body.backup_code, hashes)
        if ok:
            await db.dev_users.update_one(
                {"user_id": user["user_id"]},
                {"$set": {"mfa_backup_codes": remaining}},
            )
            user["mfa_backup_codes"] = remaining
    if not ok:
        raise HTTPException(401, "Invalid 2FA code")

    is_founder = is_founder_email(user["email"])
    is_admin = bool(user.get("is_admin")) or is_founder
    return _issue_session(user, is_admin, is_founder)


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
