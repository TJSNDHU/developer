"""
routers/oauth.py — OAuth 2.1 + PKCE for the MCP Claude Directory listing.

Endpoints (mounted at /api/aurem-dev — K8s ingress only forwards /api/*):
  GET  /.well-known/oauth-authorization-server  — RFC 8414 discovery
  GET  /oauth/authorize                         — consent / login HTML
  POST /oauth/authorize                         — submit credentials → issue code
  POST /oauth/authorize/deny                    — user cancelled
  POST /oauth/token                             — exchange code → access token (PKCE-verified)
  GET  /oauth/userinfo                          — token introspection / sub claim

Flow (RFC 6749 §4.1 + RFC 7636 S256):
  1. Claude Directory hits /oauth/authorize with ?response_type=code &client_id=&redirect_uri=
     &scope=mcp &state= &code_challenge= &code_challenge_method=S256
  2. We render a login form. User submits email + password.
  3. We verify creds with bcrypt (mirroring routers/auth.login), issue a short-lived
     auth code (10 min TTL), and 302 back to Claude's redirect_uri with ?code=&state=
  4. Claude POSTs /oauth/token with grant_type=authorization_code, code, code_verifier
  5. We verify the PKCE challenge, mark the code used, and issue an `sk-aurem-oauth-*`
     access token in db.api_keys (same collection mcp.py already consumes via
     _resolve_user → so the token works for MCP calls immediately, no second wiring).

Token TTL: 30 days. Auth code TTL: 10 min. Both enforced both by `expires_at`
check at use time AND a Mongo TTL index created in main.py lifespan so the
collections self-clean.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
import urllib.parse
from typing import Optional

import bcrypt
from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from cto_services.auth import current_dev
from cto_services.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["OAuth"])

AUTH_CODE_TTL = 600                   # 10 min
TOKEN_TTL     = 60 * 60 * 24 * 30     # 30 days
API_KEY_PREFIX = "sk-aurem-oauth-"    # distinct from manual sk-aurem-* keys


def _public_base() -> str:
    """Issuer base URL. Strips trailing slash. Defaults to prod."""
    return os.getenv("AUREM_PUBLIC_BASE_URL", "https://auremcto.com").rstrip("/")


def _issuer() -> str:
    """Issuer claim for the discovery doc — root of the API namespace."""
    return f"{_public_base()}/api/aurem-dev"


# ── Discovery (RFC 8414) ──────────────────────────────────────────────

@router.get("/.well-known/oauth-authorization-server")
async def oauth_discovery():
    base = _issuer()
    return {
        "issuer": base,
        "authorization_endpoint":            f"{base}/oauth/authorize",
        "token_endpoint":                    f"{base}/oauth/token",
        "userinfo_endpoint":                 f"{base}/oauth/userinfo",
        "response_types_supported":          ["code"],
        "grant_types_supported":             ["authorization_code"],
        "code_challenge_methods_supported":  ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported":                  ["openid", "profile", "mcp"],
    }


# ── Authorization endpoint (GET — consent page) ───────────────────────

@router.get("/oauth/authorize", response_class=HTMLResponse)
async def oauth_authorize_page(
    response_type: str = "code",
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "mcp",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    error: str = "",
):
    if response_type != "code":
        raise HTTPException(400, "unsupported_response_type — only 'code' is supported")
    if code_challenge_method != "S256":
        raise HTTPException(400, "unsupported_code_challenge_method — only 'S256' is supported")
    if not redirect_uri:
        raise HTTPException(400, "missing redirect_uri")
    if not code_challenge:
        raise HTTPException(400, "missing code_challenge (PKCE required)")

    params = urllib.parse.urlencode({
        "response_type":         response_type,
        "client_id":             client_id,
        "redirect_uri":          redirect_uri,
        "scope":                 scope,
        "state":                 state,
        "code_challenge":        code_challenge,
        "code_challenge_method": code_challenge_method,
    })

    err_block = (
        '<div style="background:#7f1d1d;color:#fecaca;padding:10px 14px;'
        'border-radius:8px;font-size:13px;margin-bottom:14px">'
        'Invalid email or password. Please try again.</div>'
    ) if error == "invalid_credentials" else ""

    # Defensive HTML-escape: scope + client_id rendered for transparency.
    safe_scope     = (scope or "mcp").replace("<", "&lt;").replace(">", "&gt;")
    safe_client_id = (client_id or "claude-desktop")[:60].replace("<", "&lt;").replace(">", "&gt;")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Connect ORA to Claude — Authorize Access</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: radial-gradient(ellipse at top, #0f172a 0%, #020617 70%);
      color: #f1f5f9;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      padding: 24px;
    }}
    .card {{
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 16px;
      padding: 36px 32px;
      max-width: 420px;
      width: 100%;
      box-shadow: 0 24px 60px rgba(0,0,0,0.5);
    }}
    .logo {{
      width: 64px;
      height: 64px;
      border-radius: 16px;
      background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #000;
      font-weight: 900;
      font-size: 22px;
      letter-spacing: -0.5px;
      margin-bottom: 22px;
    }}
    h1 {{ color: #f59e0b; font-size: 22px; margin: 0 0 6px; font-weight: 700; }}
    p.lead {{ color: #94a3b8; font-size: 14px; line-height: 1.6; margin: 0 0 18px; }}
    .scopes {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 10px;
      padding: 14px 16px;
      margin: 0 0 20px;
      font-size: 13px;
      color: #cbd5e1;
      line-height: 1.9;
    }}
    .scope-row {{ display: flex; gap: 8px; }}
    .check {{ color: #22c55e; font-weight: 700; }}
    label {{ display: block; font-size: 12px; color: #94a3b8; margin: 12px 0 4px; }}
    input {{
      width: 100%;
      padding: 11px 12px;
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 8px;
      color: #f1f5f9;
      font-size: 14px;
      transition: border 0.15s;
    }}
    input:focus {{ outline: none; border-color: #f59e0b; }}
    .btn {{
      width: 100%;
      padding: 13px;
      border-radius: 10px;
      border: none;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      margin-top: 14px;
      transition: transform 0.08s, opacity 0.15s;
    }}
    .btn:active {{ transform: translateY(1px); }}
    .btn-primary {{
      background: #f59e0b;
      color: #0a0e1a;
    }}
    .btn-primary:hover {{ opacity: 0.92; }}
    .btn-secondary {{
      background: transparent;
      border: 1px solid #334155;
      color: #94a3b8;
    }}
    .btn-secondary:hover {{ border-color: #475569; color: #cbd5e1; }}
    .footer {{ font-size: 11px; color: #475569; margin-top: 18px; text-align: center; }}
    .footer code {{ background: #1e293b; padding: 1px 5px; border-radius: 3px; color: #cbd5e1; }}
  </style>
</head>
<body>
  <div class="card" data-testid="oauth-consent-card">
    <div class="logo">ORA</div>
    <h1>Connect ORA to Claude</h1>
    <p class="lead"><b>{safe_client_id}</b> is requesting access to your ORA by Aurem CTO account.</p>
    {err_block}
    <div class="scopes">
      <div class="scope-row"><span class="check">✓</span> List your projects</div>
      <div class="scope-row"><span class="check">✓</span> Ship code tasks via MCP</div>
      <div class="scope-row"><span class="check">✓</span> View task status</div>
      <div class="scope-row"><span class="check">✓</span> View recent commits</div>
    </div>
    <form method="POST" action="/api/aurem-dev/oauth/authorize?{params}" data-testid="oauth-login-form">
      <label for="email">ORA email</label>
      <input id="email" type="email" name="email" required autocomplete="email"
             data-testid="oauth-email" />
      <label for="password">Password</label>
      <input id="password" type="password" name="password" required autocomplete="current-password"
             data-testid="oauth-password" />
      <button class="btn btn-primary" type="submit" data-testid="oauth-authorize-btn">
        Authorize ORA Access
      </button>
    </form>
    <form method="POST" action="/api/aurem-dev/oauth/authorize/deny?{params}">
      <button class="btn btn-secondary" type="submit" data-testid="oauth-deny-btn">
        Cancel
      </button>
    </form>
    <div class="footer">
      Scope requested: <code>{safe_scope}</code> · Powered by OAuth 2.1 + PKCE
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(html)


# ── Authorization endpoint (POST — process consent) ───────────────────

@router.post("/oauth/authorize")
async def oauth_authorize_submit(
    request: Request,
    response_type: str = "code",
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "mcp",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    email: str = Form(""),
    password: str = Form(""),
):
    """Verify creds (bcrypt — mirrors routers/auth.login), then issue auth code."""
    if not redirect_uri or not code_challenge:
        raise HTTPException(400, "missing redirect_uri or code_challenge")

    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    user = await db.dev_users.find_one(
        {"email": (email or "").strip().lower()},
        {"_id": 0, "user_id": 1, "email": 1, "password": 1},
    )
    bad_creds = (
        not user
        or not user.get("password")
        or not bcrypt.checkpw(password.encode(), user["password"].encode())
    )
    if bad_creds:
        # Re-show form with friendly error — preserves OAuth params
        qs = urllib.parse.urlencode({
            "response_type":         response_type,
            "client_id":             client_id,
            "redirect_uri":          redirect_uri,
            "scope":                 scope,
            "state":                 state,
            "code_challenge":        code_challenge,
            "code_challenge_method": code_challenge_method,
            "error":                 "invalid_credentials",
        })
        return RedirectResponse(
            url=f"/api/aurem-dev/oauth/authorize?{qs}",
            status_code=302,
        )

    # Issue one-time, 10-min auth code
    code = secrets.token_urlsafe(32)
    now = time.time()
    await db.oauth_codes.insert_one({
        "code":                  code,
        "user_id":               user["user_id"],
        "client_id":             client_id,
        "redirect_uri":          redirect_uri,
        "scope":                 scope,
        "code_challenge":        code_challenge,
        "code_challenge_method": code_challenge_method,
        "created_at":            now,
        "expires_at":            now + AUTH_CODE_TTL,
        "used":                  False,
    })

    sep = "&" if "?" in redirect_uri else "?"
    callback = f"{redirect_uri}{sep}code={code}"
    if state:
        callback += f"&state={urllib.parse.quote(state)}"
    logger.info(f"[oauth] issued code for user={user['user_id']} client={client_id}")
    return RedirectResponse(url=callback, status_code=302)


@router.post("/oauth/authorize/deny")
async def oauth_deny(
    redirect_uri: str = "",
    state: str = "",
):
    """User clicked Cancel — bounce back with access_denied per RFC 6749 §4.1.2.1."""
    if not redirect_uri:
        raise HTTPException(400, "missing redirect_uri")
    sep = "&" if "?" in redirect_uri else "?"
    err = f"{redirect_uri}{sep}error=access_denied"
    if state:
        err += f"&state={urllib.parse.quote(state)}"
    return RedirectResponse(url=err, status_code=302)


# ── Token endpoint ────────────────────────────────────────────────────

@router.post("/oauth/token")
async def oauth_token(
    grant_type: str  = Form(""),
    code: str        = Form(""),
    redirect_uri: str = Form(""),
    client_id: str   = Form(""),
    code_verifier: str = Form(""),
):
    """Exchange auth code for `sk-aurem-oauth-*` access token (PKCE verified)."""
    if grant_type != "authorization_code":
        raise HTTPException(400, "unsupported_grant_type")
    if not code or not code_verifier:
        raise HTTPException(400, "missing code or code_verifier")

    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    code_doc = await db.oauth_codes.find_one({"code": code, "used": False})
    if not code_doc:
        raise HTTPException(400, "invalid_grant: code not found, already used, or expired")
    if time.time() > code_doc["expires_at"]:
        raise HTTPException(400, "invalid_grant: code expired")
    if redirect_uri and code_doc.get("redirect_uri") and redirect_uri != code_doc["redirect_uri"]:
        raise HTTPException(400, "invalid_grant: redirect_uri mismatch")

    # PKCE — S256
    challenge = code_doc.get("code_challenge", "")
    method    = code_doc.get("code_challenge_method", "S256")
    if method != "S256":
        raise HTTPException(400, "unsupported_code_challenge_method")
    digest   = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    if computed != challenge:
        raise HTTPException(400, "invalid_grant: PKCE verification failed")

    # Burn the code so a replay can't reuse it
    await db.oauth_codes.update_one(
        {"code": code},
        {"$set": {"used": True, "used_at": time.time()}},
    )

    # Issue access token → reuse the api_keys collection so MCP server's
    # existing _resolve_user (mcp.py line 194) accepts it transparently.
    token = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    now   = time.time()
    await db.api_keys.insert_one({
        "key":        token,
        "user_id":    code_doc["user_id"],
        "client_id":  client_id or code_doc.get("client_id", "unknown"),
        "scope":      code_doc.get("scope", "mcp"),
        "created_at": now,
        "expires_at": now + TOKEN_TTL,
        "active":     True,
        "label":      f"Claude OAuth ({(client_id or code_doc.get('client_id', 'unknown'))[:32]})",
        "source":     "oauth",
    })

    logger.info(f"[oauth] issued access token for user={code_doc['user_id']} client={client_id}")
    return JSONResponse({
        "access_token": token,
        "token_type":   "Bearer",
        "expires_in":   TOKEN_TTL,
        "scope":        code_doc.get("scope", "mcp"),
    })


# ── Userinfo ──────────────────────────────────────────────────────────

@router.get("/oauth/userinfo")
async def oauth_userinfo(authorization: Optional[str] = Header(None)):
    """RFC 7662-ish userinfo endpoint. Accepts both JWT and sk-aurem-* tokens."""
    if not authorization:
        raise HTTPException(401, "missing authorization header")

    # Tokens from /oauth/token are sk-aurem-oauth-* → look up via api_keys.
    # Fall back to current_dev (JWT path) for backward compat.
    token = authorization.replace("Bearer ", "", 1).strip()
    db    = get_db()
    if token.startswith("sk-aurem-") and db is not None:
        key_doc = await db.api_keys.find_one(
            {"key": token, "active": True}, {"_id": 0, "user_id": 1, "scope": 1},
        )
        if not key_doc:
            raise HTTPException(401, "invalid_token")
        user = await db.dev_users.find_one(
            {"user_id": key_doc["user_id"]},
            {"_id": 0, "user_id": 1, "email": 1, "name": 1},
        )
        if not user:
            raise HTTPException(401, "user not found")
        return {
            "sub":   user["user_id"],
            "email": user.get("email", ""),
            "name":  user.get("name", ""),
            "scope": key_doc.get("scope", "mcp"),
        }

    user = await current_dev(authorization)
    return {
        "sub":   user["user_id"],
        "email": user.get("email", ""),
        "name":  user.get("name", ""),
    }
