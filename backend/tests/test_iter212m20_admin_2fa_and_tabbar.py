"""Iter 212m-20 — Admin TOTP 2FA + Home tab removal.

Covers:
  • services/mfa.py — secret gen, TOTP verify, backup-code hash + redeem
  • routers/mfa.py — enroll-start, enroll-verify, disable, status
  • routers/auth.py — single-step login still works; admins with
    mfa_enabled get gated; /auth/login/2fa-verify trades the
    short-lived mfa_token for the real session JWT
  • cto_services/auth.py — create_mfa_pending_token + consume helper
  • frontend/TabBar.jsx — Home tab removed
  • frontend/Login.jsx — 2FA challenge step wired
  • frontend/TwoFactorCard.jsx — admin enrollment UI exists +
    consumes the four mfa endpoints
"""
from __future__ import annotations

import time
from pathlib import Path

import pyotp
import pytest

from cto_services.auth import (
    create_mfa_pending_token,
    consume_mfa_pending_token,
)
from services.mfa import (
    generate_secret,
    otpauth_url,
    qr_png_base64,
    verify_code,
    generate_backup_codes,
    hash_backup_code,
    consume_backup_code,
)

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend" / "src"


# ── services/mfa.py unit tests ─────────────────────────────────────


def test_generate_secret_returns_base32_32chars():
    s = generate_secret()
    assert len(s) == 32
    assert s.isalnum()


def test_otpauth_url_includes_issuer_and_email():
    url = otpauth_url("test@aurem.dev", "ABC123")
    assert "otpauth://totp/" in url
    assert "AUREM" in url
    assert "test%40aurem.dev" in url or "test@aurem.dev" in url
    assert "secret=ABC123" in url


def test_qr_png_base64_is_data_url():
    png = qr_png_base64("otpauth://totp/x?secret=ABC")
    assert png.startswith("data:image/png;base64,")
    # The base64 segment must decode to a non-trivial PNG.
    import base64
    decoded = base64.b64decode(png.split(",", 1)[1])
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"


def test_verify_code_accepts_live_totp():
    s = generate_secret()
    live = pyotp.TOTP(s).now()
    assert verify_code(s, live) is True


def test_verify_code_rejects_wrong_code():
    s = generate_secret()
    assert verify_code(s, "000000") is False


def test_verify_code_rejects_non_digit_input():
    s = generate_secret()
    assert verify_code(s, "abcdef") is False
    assert verify_code(s, "") is False


def test_backup_codes_are_unique_and_dashed():
    codes = generate_backup_codes(8)
    assert len(set(codes)) == 8
    for c in codes:
        # 4-4-4 dash pattern
        parts = c.split("-")
        assert len(parts) == 3
        assert all(len(p) == 4 for p in parts)


def test_backup_code_hash_roundtrips():
    code = "ABCD-EFGH-JKMN"
    h = hash_backup_code(code)
    ok, remaining = consume_backup_code(code, [h])
    assert ok is True
    assert remaining == []   # consumed code is removed


def test_backup_code_consumes_only_matched_entry():
    plaintexts = generate_backup_codes(3)
    hashes = [hash_backup_code(p) for p in plaintexts]
    ok, remaining = consume_backup_code(plaintexts[1], hashes)
    assert ok is True
    assert len(remaining) == 2
    # The other two codes must still match their hashes.
    for p in (plaintexts[0], plaintexts[2]):
        ok2, _r = consume_backup_code(p, remaining)
        assert ok2 is True


def test_backup_code_rejects_unknown():
    hashes = [hash_backup_code(c) for c in generate_backup_codes(4)]
    ok, remaining = consume_backup_code("XXXX-XXXX-XXXX", hashes)
    assert ok is False
    assert len(remaining) == 4   # nothing consumed


# ── cto_services/auth.py mfa_pending token ─────────────────────────


def test_mfa_pending_token_roundtrips():
    tok = create_mfa_pending_token("u1", "test@aurem.dev")
    p = consume_mfa_pending_token(tok)
    assert p["user_id"]     == "u1"
    assert p["email"]       == "test@aurem.dev"
    assert p["mfa_pending"] is True


def test_mfa_pending_token_rejects_normal_session_jwt():
    from cto_services.auth import create_token
    from fastapi import HTTPException
    session = create_token("u1", "test@aurem.dev", is_admin=True)
    with pytest.raises(HTTPException) as ei:
        consume_mfa_pending_token(session)
    assert ei.value.status_code == 401


def test_mfa_pending_token_expiry_is_5_minutes():
    # Decode without verification to inspect the exp claim.
    import jwt as _jwt, os
    tok = create_mfa_pending_token("u1", "x@y.com")
    p = _jwt.decode(
        tok, os.getenv("JWT_SECRET", ""), algorithms=["HS256"],
        options={"verify_exp": False},
    )
    # Expiry must be ~5min (300s) ahead of now, ±10s for clock skew.
    delta = p["exp"] - int(time.time())
    assert 290 <= delta <= 310


# ── routers/mfa.py wiring pins ─────────────────────────────────────


def test_mfa_router_registered_in_main():
    src = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert "from routers.mfa import router as mfa_router" in src
    assert "app.include_router(mfa_router," in src


def test_mfa_router_exposes_four_endpoints():
    src = (BACKEND / "routers" / "mfa.py").read_text(encoding="utf-8")
    assert '@router.get("/status")' in src
    assert '@router.post("/enroll-start")' in src
    assert '@router.post("/enroll-verify")' in src
    assert '@router.post("/disable")' in src
    # And every endpoint must enforce `_require_admin`.
    assert "await _require_admin(authorization)" in src


# ── routers/auth.py wiring pins ────────────────────────────────────


def test_auth_router_login_returns_mfa_required_for_2fa_admin():
    src = (BACKEND / "routers" / "auth.py").read_text(encoding="utf-8")
    # Login must short-circuit and return an mfa_pending token instead
    # of a full session JWT when the admin has mfa_enabled.
    assert 'if is_admin and user.get("mfa_enabled") and user.get("mfa_secret")' in src
    assert '"mfa_required": True' in src
    assert "create_mfa_pending_token(" in src


def test_auth_router_has_2fa_verify_endpoint():
    src = (BACKEND / "routers" / "auth.py").read_text(encoding="utf-8")
    assert '@router.post("/login/2fa-verify")' in src
    # Must accept both 6-digit TOTP code AND a backup code.
    assert "body.backup_code" in src
    # Backup code must be removed from the user doc on success
    # (single-use enforcement).
    assert "consume_backup_code(" in src


# ── frontend: Home tab removed ─────────────────────────────────────


def test_tabbar_home_pill_removed():
    src = (FRONTEND / "components" / "TabBar.jsx").read_text(encoding="utf-8")
    # The legacy Home pill (tab-home testid) must be gone — the founder
    # asked for it to be removed so customers don't drop out of their
    # active project context.
    assert 'testid="tab-home"' not in src
    assert 'label="Home"' not in src
    # And the Home icon import must be removed (lint would otherwise
    # flag unused-imports).
    assert "from \"lucide-react\"" in src
    home_import = [
        line for line in src.splitlines()
        if "from \"lucide-react\"" in line
    ]
    for line in home_import:
        # Match a whole-word `Home,` or `Home }` in the import set.
        assert " Home," not in line and " Home }" not in line


# ── frontend: Login.jsx 2FA prompt ─────────────────────────────────


def test_login_handles_mfa_required_response():
    src = (FRONTEND / "pages" / "Login.jsx").read_text(encoding="utf-8")
    assert "mfa_required" in src
    assert "mfa_token" in src
    assert "setMfaState" in src
    assert "/auth/login/2fa-verify" in src
    # The 2FA form must be rendered with a dedicated testid so the
    # testing agent can drive it.
    assert 'data-testid="login-2fa-form"' in src
    # The 2FA code input testid is conditional (toggles between TOTP
    # and backup-code modes); assert the conditional expression.
    assert '"login-2fa-backup-input" : "login-2fa-code-input"' in src
    assert 'data-testid="login-2fa-submit"' in src
    # Backup-code path must be available.
    assert 'data-testid="login-2fa-toggle-backup"' in src
    assert "backup_code" in src


# ── frontend: TwoFactorCard.jsx ────────────────────────────────────


def test_admin_two_factor_card_exists():
    f = FRONTEND / "components" / "TwoFactorCard.jsx"
    assert f.exists()
    src = f.read_text(encoding="utf-8")
    # The card consumes all four endpoints.
    assert "/admin/2fa/status" in src
    assert "/admin/2fa/enroll-start" in src
    assert "/admin/2fa/enroll-verify" in src
    assert "/admin/2fa/disable" in src
    # QR + secret + backup codes must surface as DOM nodes with
    # testids so the testing agent can find them.
    assert 'data-testid="admin-2fa-qr"' in src
    assert 'data-testid="admin-2fa-secret"' in src
    assert 'data-testid="admin-2fa-backup-codes"' in src
    assert 'data-testid="admin-2fa-confirm-submit"' in src
    assert 'data-testid="admin-2fa-disable-cta"' in src


def test_admin_settings_mounts_two_factor_card():
    src = (FRONTEND / "pages" / "Admin.jsx").read_text(encoding="utf-8")
    assert 'import TwoFactorCard from "../components/TwoFactorCard"' in src
    assert "<TwoFactorCard />" in src


# ── requirements.txt ──────────────────────────────────────────────


def test_requirements_has_pyotp_and_qrcode():
    reqs = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
    assert "pyotp" in reqs
    assert "qrcode" in reqs
