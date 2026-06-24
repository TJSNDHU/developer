"""routers/mfa.py — Iter 212m-20

Admin 2FA (TOTP) enrollment + management endpoints.

Three endpoints:

  POST /api/aurem-dev/admin/2fa/enroll-start
      Admin-only. Generates a fresh TOTP secret + QR code + backup
      codes, stashes the secret under `mfa_secret_pending` so a
      half-finished enrollment can be retried. Does NOT enable 2FA
      yet — that happens on /enroll-verify.

  POST /api/aurem-dev/admin/2fa/enroll-verify { code }
      Admin scans the QR in their authenticator app, types the
      6-digit code from the pending secret. On success we copy
      `mfa_secret_pending` → `mfa_secret`, hash the backup codes,
      flip `mfa_enabled=True`, and clear the pending fields.

  POST /api/aurem-dev/admin/2fa/disable { code }
      Admin must prove they still have the authenticator (or supply
      a valid backup code) before 2FA can be turned off, so a
      compromised session token alone can't lift the protection.

  GET  /api/aurem-dev/admin/2fa/status
      Returns { enabled, has_pending, backup_codes_remaining }.

Header auth: standard `Authorization: Bearer <jwt>`. `is_admin=True`
is enforced via `_require_admin`. Founders are admins by definition.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.mfa import (
    generate_secret,
    otpauth_url,
    qr_png_base64,
    verify_code,
    generate_backup_codes,
    hash_backup_code,
    consume_backup_code,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/2fa", tags=["Admin 2FA"])


async def _require_admin(authorization: Optional[str]) -> dict:
    payload = await current_dev(authorization)
    if not payload.get("is_admin"):
        raise HTTPException(403, "Admin only")
    return payload


class CodeBody(BaseModel):
    code: Optional[str] = None
    backup_code: Optional[str] = None


# ── Status ─────────────────────────────────────────────────────────


@router.get("/status")
async def status(authorization: Optional[str] = Header(None)) -> dict:
    payload = await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    u = await db.dev_users.find_one(
        {"user_id": payload["user_id"]},
        {"_id": 0, "mfa_enabled": 1, "mfa_secret_pending": 1,
         "mfa_backup_codes": 1},
    ) or {}
    return {
        "enabled":                 bool(u.get("mfa_enabled")),
        "has_pending":             bool(u.get("mfa_secret_pending")),
        "backup_codes_remaining":  len(u.get("mfa_backup_codes") or []),
    }


# ── Enrollment ─────────────────────────────────────────────────────


@router.post("/enroll-start")
async def enroll_start(authorization: Optional[str] = Header(None)) -> dict:
    """Begin a fresh 2FA enrollment. Returns the secret + QR PNG +
    8 plaintext backup codes (shown ONCE, never returned again).

    Idempotent within a session: calling start twice rolls a new
    secret AND new backup codes; the previous pending secret is
    overwritten so the admin can recover from a closed/refreshed
    enrollment dialog by starting over."""
    payload = await _require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    u = await db.dev_users.find_one(
        {"user_id": payload["user_id"]}, {"_id": 0, "email": 1, "mfa_enabled": 1},
    )
    if not u:
        raise HTTPException(404, "User not found")
    if u.get("mfa_enabled"):
        raise HTTPException(
            409,
            "2FA already enabled — call /admin/2fa/disable first to re-enroll",
        )
    secret = generate_secret()
    backup_plain = generate_backup_codes(8)
    backup_hashes = [hash_backup_code(c) for c in backup_plain]
    # Stash pending secret + backup hashes. mfa_enabled stays False
    # until /enroll-verify confirms the user actually scanned the QR.
    await db.dev_users.update_one(
        {"user_id": payload["user_id"]},
        {"$set": {
            "mfa_secret_pending":        secret,
            "mfa_backup_codes_pending":  backup_hashes,
        }},
    )
    url = otpauth_url(u["email"], secret)
    return {
        "ok":           True,
        "otpauth_url":  url,
        "qr_png":       qr_png_base64(url),
        "secret":       secret,        # for manual entry into the auth app
        "backup_codes": backup_plain,  # SHOWN ONCE — front-end must persist
    }


@router.post("/enroll-verify")
async def enroll_verify(
    body: CodeBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Admin scanned the QR + typed the 6-digit code. On success we
    promote the pending secret to the live one and flip
    `mfa_enabled=True`."""
    payload = await _require_admin(authorization)
    if not body.code:
        raise HTTPException(400, "`code` (6-digit TOTP) is required")
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    u = await db.dev_users.find_one(
        {"user_id": payload["user_id"]},
        {"_id": 0, "mfa_secret_pending": 1, "mfa_backup_codes_pending": 1},
    ) or {}
    pending = u.get("mfa_secret_pending")
    if not pending:
        raise HTTPException(
            409, "No pending enrollment — call /enroll-start first",
        )
    if not verify_code(pending, body.code):
        raise HTTPException(401, "Invalid 2FA code")
    await db.dev_users.update_one(
        {"user_id": payload["user_id"]},
        {
            "$set": {
                "mfa_enabled":      True,
                "mfa_secret":       pending,
                "mfa_backup_codes": u.get("mfa_backup_codes_pending") or [],
            },
            "$unset": {
                "mfa_secret_pending":        "",
                "mfa_backup_codes_pending":  "",
            },
        },
    )
    return {"ok": True, "enabled": True}


@router.post("/disable")
async def disable(
    body: CodeBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Admin must prove possession of the authenticator (TOTP) OR a
    valid backup code before 2FA can be lifted."""
    payload = await _require_admin(authorization)
    if not body.code and not body.backup_code:
        raise HTTPException(400, "Provide either `code` or `backup_code`")
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    u = await db.dev_users.find_one(
        {"user_id": payload["user_id"]},
        {"_id": 0, "mfa_enabled": 1, "mfa_secret": 1, "mfa_backup_codes": 1},
    ) or {}
    if not u.get("mfa_enabled"):
        return {"ok": True, "enabled": False, "noop": True}

    ok = False
    if body.code:
        ok = verify_code(u.get("mfa_secret") or "", body.code)
    if not ok and body.backup_code:
        ok, _remaining = consume_backup_code(
            body.backup_code, u.get("mfa_backup_codes") or [],
        )
    if not ok:
        raise HTTPException(401, "Invalid 2FA code")

    await db.dev_users.update_one(
        {"user_id": payload["user_id"]},
        {
            "$set": {"mfa_enabled": False},
            "$unset": {
                "mfa_secret":              "",
                "mfa_backup_codes":        "",
                "mfa_secret_pending":      "",
                "mfa_backup_codes_pending": "",
            },
        },
    )
    return {"ok": True, "enabled": False}
