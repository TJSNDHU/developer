"""services/mfa.py — Iter 212m-20

TOTP-based two-factor authentication for admin accounts.

Uses pyotp (RFC 6238) + qrcode — fully local, no external service.
Compatible with Google Authenticator, 1Password, Authy, Bitwarden,
Microsoft Authenticator, any RFC-compliant TOTP app.

Surface area kept minimal so the auth router can stay thin:
  generate_secret()                 → base32 string (the TOTP seed)
  otpauth_url(email, secret)        → otpauth:// URL for QR encoding
  qr_png_base64(url)                → "data:image/png;base64,…"
  verify_code(secret, code)         → bool
  generate_backup_codes(n=8)        → list of plaintext codes (shown once)
  hash_backup_code(code)            → bcrypt hash (matches dev_users.password)
  consume_backup_code(code, hashes) → (ok: bool, remaining_hashes: list[str])
"""
from __future__ import annotations

import base64
import io
import secrets
from typing import Iterable

import bcrypt
import pyotp
import qrcode


_APP_NAME = "AUREM CTO"


def generate_secret() -> str:
    """Random 160-bit (32-char base32) TOTP secret — RFC 4226 recommended."""
    return pyotp.random_base32()


def otpauth_url(email: str, secret: str) -> str:
    """Build the `otpauth://totp/...` URL the QR encodes."""
    return pyotp.TOTP(secret).provisioning_uri(
        name=email, issuer_name=_APP_NAME,
    )


def qr_png_base64(otpauth: str) -> str:
    """Render `otpauth` as a PNG and return a `data:image/png;base64,…`
    string so the frontend can stuff it straight into an <img>."""
    img = qrcode.make(otpauth, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def verify_code(secret: str, code: str) -> bool:
    """RFC 6238 TOTP check with ±1 window (30s either side) so a code
    typed at the boundary still passes. False on empty/non-digit input."""
    if not secret or not code:
        return False
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != 6:
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=1)
    except Exception:
        return False


# ── Backup recovery codes ──────────────────────────────────────────


def _new_backup_code() -> str:
    """10-char alphanumeric, dash-grouped (`A3F7-2K9P-XQ4M`) so the
    user can read/copy it from a printed sheet."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I — easier to read
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


def generate_backup_codes(n: int = 8) -> list[str]:
    """Plaintext codes — show ONCE during enrollment, then store only
    the bcrypt hashes."""
    return [_new_backup_code() for _ in range(n)]


def hash_backup_code(code: str) -> str:
    """Bcrypt-hash a backup code so a DB leak doesn't expose them."""
    norm = (code or "").strip().upper()
    return bcrypt.hashpw(norm.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def consume_backup_code(
    submitted: str, hashes: Iterable[str],
) -> tuple[bool, list[str]]:
    """Single-use redemption. If `submitted` matches any of `hashes`,
    return `(True, hashes - matched)`. Otherwise `(False, list(hashes))`.
    Hashes are bcrypt strings (see `hash_backup_code`)."""
    norm = (submitted or "").strip().upper().encode("utf-8")
    out: list[str] = []
    matched = False
    for h in hashes:
        if matched or not h:
            if h:
                out.append(h)
            continue
        try:
            if bcrypt.checkpw(norm, h.encode("utf-8")):
                matched = True   # drop this one
                continue
        except Exception:
            pass
        out.append(h)
    return matched, out
