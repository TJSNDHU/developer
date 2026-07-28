"""Iter 337 — /auth/me must never leak auth secrets (found LIVE on
prod during founder-session verification: response contained the TOTP
mfa_secret, hashed mfa_backup_codes and the raw GitHub access_token).
"""
from pathlib import Path


def test_me_strips_secrets_source_lock():
    src = Path("/app/backend/routers/auth.py").read_text()
    seg = src.split('@router.get("/me")')[1].split("@router.get")[0]
    assert '"mfa_secret"' in seg and '"mfa_backup_codes"' in seg
    assert 'gh.pop("access_token", None)' in seg


def test_me_live_response_has_no_secrets():
    import httpx
    base = "http://localhost:8001/api/aurem-dev"
    r = httpx.post(f"{base}/auth/login", json={
        "email": "test@aurem.dev", "password": "AuremTest2026!"},
        timeout=20)
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok, f"login gave no token: {r.json()}"
    me = httpx.get(f"{base}/auth/me",
                   headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    assert me.status_code == 200
    user = me.json()["user"]
    assert "mfa_secret" not in user
    assert "mfa_backup_codes" not in user
    assert "password" not in user
    gh = user.get("github") or {}
    assert "access_token" not in gh
