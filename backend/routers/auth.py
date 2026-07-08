"""
routers/auth.py — AUREM Dev
Developer signup, login, token endpoints.
"""
from __future__ import annotations
import uuid
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import bcrypt
import httpx
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel

from cto_services.auth import (
    create_token, current_dev, create_mfa_pending_token,
    consume_mfa_pending_token,
)
from cto_services.db import get_db
from services.usage import is_founder_email
from services.mfa import verify_code, consume_backup_code
from services.rate_limiter import check_rate_limit, client_ip_from_request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


# Iter 212m-48 — brute-force protection on /auth/login.
# All knobs are env-tunable so the lockout can be tightened/loosened
# in prod without a code change.
_LOGIN_RATE_PER_MIN  = int(os.getenv("LOGIN_RATE_PER_MIN", "10"))
_LOGIN_FAIL_LIMIT    = int(os.getenv("LOGIN_FAIL_LIMIT", "5"))
_LOGIN_LOCKOUT_MIN   = int(os.getenv("LOGIN_LOCKOUT_MIN", "15"))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _enforce_login_guard(db, client_ip: str) -> None:
    """Iter 212m-48 — combined per-IP rate-limit + sliding-window
    failed-attempts lockout for /auth/login. Called BEFORE the
    email/password check so an attacker can't differentiate
    "valid-but-locked" from "invalid creds" via timing.

    Two layers:
      (1) In-memory sliding window: LOGIN_RATE_PER_MIN (default 10) hits
          per IP per minute → 429. Keeps cost cheap and survives a
          process restart only as long as the bucket lives.
      (2) Mongo-persisted lockout: LOGIN_FAIL_LIMIT (default 5) failed
          attempts inside LOGIN_LOCKOUT_MIN (default 15) minutes → 429
          for the remainder of the window. Survives restarts.
    """
    # Layer 1 — burst rate limit
    if not check_rate_limit(f"login-ip:{client_ip}", _LOGIN_RATE_PER_MIN):
        raise HTTPException(
            429,
            f"Too many login attempts from this IP. "
            f"Slow down — limit is {_LOGIN_RATE_PER_MIN} per minute.",
        )
    # Layer 2 — persisted lockout window
    if db is None:
        return
    row = await db.login_attempts.find_one({"_id": f"ip:{client_ip}"})
    if not row:
        return
    fails: list = list(row.get("failed_at") or [])
    cutoff = _now_utc()
    # Mongo can hand back naive datetimes for legacy rows written before
    # we standardised on tz-aware UTC. Normalise both sides of the
    # subtraction so the lockout window comparison never raises.
    def _as_aware(ts):
        if isinstance(ts, datetime):
            return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
        return None
    # Drop entries older than the lockout window so the counter
    # auto-resets after `_LOGIN_LOCKOUT_MIN` of quiet behaviour.
    fresh = []
    for ts in fails:
        aware = _as_aware(ts)
        if aware is None:
            continue
        if (cutoff - aware).total_seconds() <= _LOGIN_LOCKOUT_MIN * 60:
            fresh.append(aware)
    if len(fresh) >= _LOGIN_FAIL_LIMIT:
        oldest = min(fresh)
        retry_in = int(
            (_LOGIN_LOCKOUT_MIN * 60) - (cutoff - oldest).total_seconds(),
        )
        retry_in = max(retry_in, 30)
        raise HTTPException(
            429,
            f"Too many failed logins from this IP. "
            f"Try again in ~{retry_in // 60 + 1} minute(s).",
        )


async def _record_login_failure(db, client_ip: str, email: str) -> None:
    """Append a failed-attempt timestamp to both the IP-keyed lockout
    row and (best-effort) the user row. We do NOT store the password
    attempt — only timestamps + email — so this log is safe to expose
    via admin tooling later."""
    if db is None:
        return
    now = _now_utc()
    try:
        await db.login_attempts.update_one(
            {"_id": f"ip:{client_ip}"},
            {
                "$push": {"failed_at": {"$each": [now], "$slice": -50}},
                "$set":  {"last_email": email, "last_failed_at": now},
            },
            upsert=True,
        )
    except Exception as _e:
        logger.warning("login_attempts IP update failed: %r", _e)
    try:
        await db.dev_users.update_one(
            {"email": email},
            {"$inc": {"failed_logins": 1}, "$set": {"last_failed_at": now}},
        )
    except Exception as _e:
        logger.warning("dev_users failed_logins update failed: %r", _e)


async def _clear_login_failures(db, client_ip: str, email: str) -> None:
    """Reset the lockout counters on a successful login so the user
    isn't penalised for stale failures from the same IP."""
    if db is None:
        return
    try:
        await db.login_attempts.delete_one({"_id": f"ip:{client_ip}"})
    except Exception:
        pass
    try:
        await db.dev_users.update_one(
            {"email": email},
            {"$set": {"failed_logins": 0}},
        )
    except Exception:
        pass


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
    # Iter 212m-70 — projection: signup only needs the existence check
    # plus the email field for the duplicate-message logic.  No need
    # to pull password_hash / failed_logins / tokens_remaining etc.
    existing = await db.dev_users.find_one(
        {"email": body.email}, {"_id": 0, "email": 1},
    )
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
    created_at = datetime.now(timezone.utc)
    await db.dev_users.insert_one({
        "user_id": user_id,
        "email": body.email,
        "name": body.name or body.email.split("@")[0],
        "password": hashed,
        "tier": tier,
        "tokens_remaining": starting_tokens,
        "is_admin": is_founder,
        "is_unlimited": is_founder,
        # Iter 212m-30 — `created_at` powers the 3-day chat-bg tint and
        # the founder-offer `days_since_signup` check. Stored as tz-aware
        # datetime so Mongo's BSON serialiser keeps the timezone intact.
        "created_at": created_at,
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
        "created_at": created_at.isoformat(),
    }


# ── Google OAuth (Emergent-managed) ──────────────────────────────────
# One-click signup/sign-in. The frontend sends the user to
# auth.emergentagent.com which returns a `session_id` in the URL
# fragment; the browser posts it here and we exchange it (server-side
# ONLY) for the verified Google profile, then bridge into our OWN
# dev_users model + mint our OWN app JWT — same pattern as GitHub OAuth
# so the whole app keeps a single auth/token system.
_EMERGENT_SESSION_URL = (
    "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"
)


class GoogleSessionBody(BaseModel):
    session_id: str


@router.post("/google/session")
async def google_session(body: GoogleSessionBody) -> dict:
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    sid = (body.session_id or "").strip()
    if not sid:
        raise HTTPException(400, "Missing session_id")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                _EMERGENT_SESSION_URL, headers={"X-Session-ID": sid},
            )
    except Exception:                                       # noqa: BLE001
        raise HTTPException(502, "Could not reach Google auth service")
    if r.status_code != 200:
        raise HTTPException(401, "Google session invalid or expired")
    data = r.json() or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(401, "Google profile missing email")
    name = data.get("name") or email.split("@")[0]
    picture = data.get("picture") or ""

    existing = await db.dev_users.find_one({"email": email}, {"_id": 0})
    if existing:
        user_id  = existing["user_id"]
        is_admin = bool(existing.get("is_admin"))
        tier     = existing.get("tier") or "free"
        tokens   = existing.get("tokens_remaining")
        is_new   = False
        await db.dev_users.update_one(
            {"user_id": user_id},
            {"$set": {"google": {
                "name": name, "picture": picture,
                "connected_at": datetime.now(timezone.utc),
            }}},
        )
    else:
        is_new     = True
        user_id    = uuid.uuid4().hex
        is_founder = is_founder_email(email)
        is_admin   = is_founder
        tier       = "founder" if is_founder else "free"
        tokens     = 10**9 if is_founder else 1000
        created_at = datetime.now(timezone.utc)
        await db.dev_users.insert_one({
            "user_id":          user_id,
            "email":            email,
            "name":             name,
            "password":         None,          # OAuth-only user
            "auth_provider":    "google",
            "tier":             tier,
            "tokens_remaining": tokens,
            "is_admin":         is_admin,
            "is_unlimited":     is_admin,
            "created_at":       created_at,
            "google": {
                "name": name, "picture": picture,
                "connected_at": created_at,
            },
        })
    token = create_token(user_id, email, is_admin=is_admin)
    return {
        "ok":               True,
        "token":            token,
        "user_id":          user_id,
        "email":            email,
        "name":             name,
        "tier":             tier,
        "tokens_remaining": tokens,
        "is_admin":         is_admin,
        "is_unlimited":     is_admin,
        "new":              is_new,
    }


@router.post("/login")
async def login(body: LoginBody, request: Request) -> dict:
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    # Iter 212m-48 — brute-force protection. Enforce BEFORE the email
    # lookup so an attacker can't probe for valid emails by timing.
    client_ip = client_ip_from_request(request)
    await _enforce_login_guard(db, client_ip)
    user = await db.dev_users.find_one({"email": body.email}, {"_id": 0})
    if not user:
        await _record_login_failure(db, client_ip, body.email)
        raise HTTPException(401, "Invalid credentials")
    # OAuth-only accounts have no password — block password sign-in for
    # them with a clear message so they go through the GitHub button.
    if not user.get("password"):
        raise HTTPException(
            401,
            "This account uses GitHub sign-in. Use 'Continue with GitHub'.",
        )
    if not bcrypt.checkpw(body.password.encode(), user["password"].encode()):
        await _record_login_failure(db, client_ip, body.email)
        raise HTTPException(401, "Invalid credentials")
    # Iter 212m-48 — password check passed. Clear the lockout state for
    # this IP / user so a stale string of failures doesn't carry over.
    await _clear_login_failures(db, client_ip, body.email)
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
    # Iter 212m-30 — coerce datetime fields to ISO strings so the JSON
    # serialiser doesn't reject the response. `created_at` is read by
    # the frontend to compute the founder welcome tint. Some legacy
    # rows have an epoch float instead of a datetime — we leave those
    # numbers untouched; getChatBgTint() handles both shapes.
    if user:
        ts = user.get("created_at")
        if isinstance(ts, datetime):
            user["created_at"] = ts.isoformat()
    # Iter 212m-48 — auto-refresh the session token on every /auth/me
    # call. The frontend already hits this on app boot and on focus,
    # so active users glide indefinitely while idle / leaked tokens
    # die within the new 7-day window.
    fresh_token = create_token(
        payload["user_id"],
        payload.get("email", (user or {}).get("email", "")),
        is_admin=bool((user or {}).get("is_admin") or payload.get("is_admin")),
    )
    return {"ok": True, "user": user or payload, "token": fresh_token}


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
