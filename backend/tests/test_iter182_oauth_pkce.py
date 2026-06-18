"""
test_iter182_oauth_pkce.py — OAuth 2.1 + PKCE for Claude Directory.

Verifies the RFC 6749 §4.1 + RFC 7636 (S256) flow end-to-end against
the running backend on port 8001:

  1. /.well-known/oauth-authorization-server — RFC 8414 discovery
  2. GET /oauth/authorize — HTML consent page renders with required fields
  3. POST /oauth/authorize — valid creds + PKCE challenge → 302 with ?code=
  4. POST /oauth/token — valid code + matching code_verifier → access_token
  5. PKCE failure — wrong verifier on a fresh code → 400 invalid_grant
  6. Replay — re-using a burned code → 400
  7. MCP transparency — issued sk-aurem-oauth-* token works as bearer on
     /api/aurem-dev/mcp (initialize returns serverInfo).
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse

import httpx
import pytest

BASE = "http://127.0.0.1:8001/api/aurem-dev"


def _pkce_pair() -> tuple[str, str]:
    verifier  = secrets.token_urlsafe(64)
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _make_user(client: httpx.Client) -> tuple[str, str]:
    email = f"oauth-pytest-{int(time.time()*1000)}-{secrets.token_hex(3)}@aurem.dev"
    pw    = "PyTestPass123!"
    r = client.post(
        f"{BASE}/auth/signup",
        json={"email": email, "password": pw, "name": "OAuth PyTest"},
        timeout=10.0,
    )
    assert r.status_code == 200, f"signup failed: {r.status_code} {r.text}"
    return email, pw


@pytest.fixture(scope="module")
def client():
    with httpx.Client(follow_redirects=False) as c:
        yield c


def test_1_discovery_doc(client):
    r = client.get(f"{BASE}/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    d = r.json()
    assert d["issuer"].endswith("/api/aurem-dev")
    assert d["authorization_endpoint"].endswith("/oauth/authorize")
    assert d["token_endpoint"].endswith("/oauth/token")
    assert d["userinfo_endpoint"].endswith("/oauth/userinfo")
    assert d["response_types_supported"] == ["code"]
    assert d["grant_types_supported"] == ["authorization_code"]
    assert d["code_challenge_methods_supported"] == ["S256"]
    assert "mcp" in d["scopes_supported"]


def test_2_consent_page_renders(client):
    _, challenge = _pkce_pair()
    r = client.get(
        f"{BASE}/oauth/authorize",
        params={
            "response_type":         "code",
            "client_id":             "claude-desktop",
            "redirect_uri":          "https://claude.ai/cb",
            "scope":                 "mcp",
            "state":                 "abc",
            "code_challenge":        challenge,
            "code_challenge_method": "S256",
        },
    )
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Authorize ORA Access" in body
    assert 'name="email"' in body
    assert 'name="password"' in body
    assert 'oauth-authorize-btn' in body  # data-testid


def test_3_consent_page_rejects_missing_pkce(client):
    r = client.get(
        f"{BASE}/oauth/authorize",
        params={
            "response_type": "code",
            "redirect_uri":  "https://claude.ai/cb",
            # no code_challenge
        },
    )
    assert r.status_code == 400
    assert "code_challenge" in r.text.lower() or "pkce" in r.text.lower()


def test_4_consent_page_rejects_plain_pkce_method(client):
    _, challenge = _pkce_pair()
    r = client.get(
        f"{BASE}/oauth/authorize",
        params={
            "response_type":         "code",
            "redirect_uri":          "https://claude.ai/cb",
            "code_challenge":        challenge,
            "code_challenge_method": "plain",
        },
    )
    assert r.status_code == 400
    assert "S256" in r.text or "code_challenge_method" in r.text.lower()


def test_5_full_flow_happy_path(client):
    email, pw = _make_user(client)
    verifier, challenge = _pkce_pair()
    qs = {
        "response_type":         "code",
        "client_id":             "claude-desktop",
        "redirect_uri":          "https://claude.ai/cb",
        "scope":                 "mcp",
        "state":                 "STATE-xyz",
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }

    # POST /oauth/authorize with valid creds → 302 + ?code=&state=
    r = client.post(
        f"{BASE}/oauth/authorize",
        params=qs,
        data={"email": email, "password": pw},
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    parsed = urllib.parse.urlparse(loc)
    q = urllib.parse.parse_qs(parsed.query)
    assert parsed.netloc == "claude.ai"
    assert "code" in q and q["code"][0]
    assert q["state"] == ["STATE-xyz"]
    code = q["code"][0]

    # POST /oauth/token with matching verifier → access_token
    r = client.post(
        f"{BASE}/oauth/token",
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  "https://claude.ai/cb",
            "client_id":     "claude-desktop",
            "code_verifier": verifier,
        },
    )
    assert r.status_code == 200, f"token exchange failed: {r.text}"
    tok = r.json()
    assert tok["access_token"].startswith("sk-aurem-oauth-")
    assert tok["token_type"] == "Bearer"
    assert tok["expires_in"] == 30 * 24 * 60 * 60
    assert tok["scope"] == "mcp"

    # token works on MCP server
    r = client.post(
        f"{BASE}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Authorization": f"Bearer {tok['access_token']}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["serverInfo"]["name"]

    # /oauth/userinfo with our token
    r = client.get(
        f"{BASE}/oauth/userinfo",
        headers={"Authorization": f"Bearer {tok['access_token']}"},
    )
    assert r.status_code == 200
    ui = r.json()
    assert ui["email"] == email
    assert ui["scope"] == "mcp"

    # replay rejected
    r = client.post(
        f"{BASE}/oauth/token",
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  "https://claude.ai/cb",
            "client_id":     "claude-desktop",
            "code_verifier": verifier,
        },
    )
    assert r.status_code == 400
    assert "invalid_grant" in r.text


def test_6_pkce_failure_wrong_verifier(client):
    """The critical PKCE security check — wrong verifier must reject."""
    email, pw = _make_user(client)
    verifier, challenge = _pkce_pair()
    qs = {
        "response_type":         "code",
        "client_id":             "claude-desktop",
        "redirect_uri":          "https://claude.ai/cb",
        "scope":                 "mcp",
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }

    r = client.post(
        f"{BASE}/oauth/authorize",
        params=qs,
        data={"email": email, "password": pw},
    )
    assert r.status_code == 302
    code = urllib.parse.parse_qs(
        urllib.parse.urlparse(r.headers["location"]).query
    )["code"][0]

    # Submit a DIFFERENT verifier — PKCE must fail
    bad_verifier = secrets.token_urlsafe(64)
    assert bad_verifier != verifier  # sanity
    r = client.post(
        f"{BASE}/oauth/token",
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  "https://claude.ai/cb",
            "client_id":     "claude-desktop",
            "code_verifier": bad_verifier,
        },
    )
    assert r.status_code == 400
    assert "PKCE" in r.text or "invalid_grant" in r.text


def test_7_invalid_credentials_redirect_back_to_form(client):
    _, challenge = _pkce_pair()
    qs = {
        "response_type":         "code",
        "client_id":             "claude-desktop",
        "redirect_uri":          "https://claude.ai/cb",
        "scope":                 "mcp",
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    r = client.post(
        f"{BASE}/oauth/authorize",
        params=qs,
        data={"email": "nobody@example.com", "password": "wrong"},
    )
    assert r.status_code == 302
    # Should bounce back to the consent page with ?error=invalid_credentials,
    # NOT to claude.ai with a code.
    loc = r.headers["location"]
    parsed = urllib.parse.urlparse(loc)
    # path-based check (the encoded redirect_uri param contains
    # "claude.ai" as a substring — checking path is the only correct
    # way to assert we bounced internally).
    assert parsed.path.endswith("/oauth/authorize"), f"expected internal bounce, got {loc}"
    assert "error=invalid_credentials" in loc
    # No `code=` param — only an error
    q = urllib.parse.parse_qs(parsed.query)
    assert "code" not in q


def test_8_deny_returns_access_denied(client):
    r = client.post(
        f"{BASE}/oauth/authorize/deny",
        params={
            "redirect_uri": "https://claude.ai/cb",
            "state":        "STATE-xyz",
        },
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert "claude.ai" in loc
    assert "error=access_denied" in loc
    assert "state=STATE-xyz" in loc


def test_9_mcp_manifest_exposes_oauth_block(client):
    """MCP clients (Claude Desktop) read /mcp to discover OAuth without
    a second round-trip to /.well-known."""
    r = client.get(f"{BASE}/mcp")
    assert r.status_code == 200
    d = r.json()
    assert "oauth" in d
    o = d["oauth"]
    assert o["pkce_required"] is True
    assert o["code_challenge_methods"] == ["S256"]
    assert o["authorization_endpoint"].endswith("/oauth/authorize")
    assert o["token_endpoint"].endswith("/oauth/token")
