"""
test_iter82_oauth_signup.py — GitHub OAuth-first signup / sign-in.

Bug fixed: the "Continue with GitHub" button on /login and /signup hit
`/api/aurem-dev/github/oauth/connect?signup=1` from an unauthenticated
browser navigation. The endpoint blindly required a JWT and returned
401 "Authorization header missing".

New behaviour:
  • /connect?signup=1   → no JWT required, state encodes `signup:{nonce}`,
                          303 redirect to GitHub.
  • /connect            → still requires JWT (Settings "connect" flow).
  • /callback           → for `signup:` state, exchanges code, finds or
                          creates a dev_users row, issues JWT, redirects
                          to /oauth-finish with token in URL fragment.

Plus a couple of small auxiliaries:
  • Password sign-in now refuses OAuth-only accounts with a clear msg.
  • SCOPES updated to include `user:email` so /user/emails works.
  • Frontend route `/oauth-finish` exists and stashes the token.
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest

API = "http://localhost:8001/api/aurem-dev"
BASE_FRONT = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(rel: str) -> str:
    with open(os.path.join(BASE_FRONT, rel), encoding="utf-8") as fh:
        return fh.read()


# ── 1. The original bug — /connect?signup=1 must NOT require auth ────

@pytest.mark.asyncio
async def test_connect_signup_no_auth_required():
    """The exact regression: unauthenticated GET should now bounce
    to GitHub (HTTP 3xx), not 401."""
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as c:
        r = await c.get(f"{API}/github/oauth/connect?signup=1")
    assert r.status_code in (302, 303, 307), (
        f"expected redirect, got {r.status_code}: {r.text[:200]}"
    )
    loc = r.headers.get("location") or ""
    assert "github.com/login/oauth/authorize" in loc
    assert "state=signup%3A" in loc or "state=signup:" in loc
    # The new auth scope must be advertised so /user/emails works.
    assert "user%3Aemail" in loc or "user:email" in loc


@pytest.mark.asyncio
async def test_connect_signup_persists_state_row():
    """The state nonce must be recorded so /callback can validate it."""
    from motor.motor_asyncio import AsyncIOMotorClient
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as c:
        r = await c.get(f"{API}/github/oauth/connect?signup=1")
    loc = r.headers.get("location") or ""
    # Pull state= from the URL
    import urllib.parse as up
    qs = up.parse_qs(up.urlparse(loc).query)
    state = qs.get("state", [""])[0]
    assert state.startswith("signup:")
    row = await db.oauth_states.find_one({"state": state})
    assert row is not None
    assert row.get("mode") == "signup"
    # Cleanup
    await db.oauth_states.delete_one({"state": state})


# ── 2. /connect (no `?signup`) still requires auth ────────────────────

@pytest.mark.asyncio
async def test_connect_settings_flow_still_requires_auth():
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as c:
        r = await c.get(f"{API}/github/oauth/connect")
    assert r.status_code == 401


# ── 3. /callback rejects unknown state ────────────────────────────────

@pytest.mark.asyncio
async def test_callback_rejects_unknown_state():
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{API}/github/oauth/callback",
            params={"code": "anything", "state": "signup:not-a-real-nonce"},
        )
    assert r.status_code == 400
    body = r.json()
    assert "state" in (body.get("detail") or "").lower()


# ── 4. Password sign-in must block OAuth-only accounts ────────────────

@pytest.mark.asyncio
async def test_login_blocks_oauth_only_account():
    """Insert a passwordless OAuth user, then try to log in with a
    password — must return 401 with the GitHub hint."""
    from motor.motor_asyncio import AsyncIOMotorClient
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]
    email = f"oauth_only_{uuid.uuid4().hex[:8]}@aurem.test"
    await db.dev_users.insert_one({
        "user_id": uuid.uuid4().hex,
        "email": email,
        "name": email.split("@")[0],
        "password": None,
        "auth_provider": "github",
        "tier": "free",
        "tokens_remaining": 1000,
    })
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(f"{API}/auth/login", json={
                "email": email, "password": "guessing",
            })
        assert r.status_code == 401
        assert "GitHub" in (r.json().get("detail") or "")
    finally:
        await db.dev_users.delete_many({"email": email})


# ── 5. Frontend wiring locks ──────────────────────────────────────────

def test_app_route_for_oauth_finish_registered():
    src = _read("frontend/src/App.jsx")
    assert "import OAuthFinish" in src
    assert 'path="/oauth-finish"' in src


def test_oauth_finish_page_stashes_token_and_redirects():
    src = _read("frontend/src/pages/OAuthFinish.jsx")
    # Reads token from the URL fragment (#token=…)
    assert "window.location.hash" in src
    assert "setToken(token)" in src
    # Sets the just-logged-in flag for the PWA prompt to pick up.
    assert "aurem_just_logged_in" in src
    # Sends the user to /dashboard, not /login, on success.
    assert '"/dashboard"' in src
    # Clears the fragment so a refresh can't replay.
    assert "history.replaceState" in src


def test_pwa_install_prompt_component_exists():
    src = _read("frontend/src/components/PWAInstallPrompt.jsx")
    assert "beforeinstallprompt" in src
    assert "appinstalled" in src
    # Display-mode check so we don't pester an already-installed PWA.
    assert "display-mode: standalone" in src
    # Three required testids for QA / automation.
    for tid in ("pwa-install-prompt", "pwa-install-confirm",
                "pwa-install-dismiss"):
        assert f'data-testid="{tid}"' in src


def test_pwa_prompt_mounted_in_shell_when_authed():
    src = _read("frontend/src/components/Shell.jsx")
    assert "PWAInstallPrompt" in src
    # Only render for an authenticated user.
    assert "{token && <PWAInstallPrompt />}" in src


def test_email_password_login_sets_just_logged_in_flag():
    src = _read("frontend/src/pages/Login.jsx")
    assert 'localStorage.setItem("aurem_just_logged_in", "1")' in src


def test_email_password_signup_sets_just_logged_in_flag():
    src = _read("frontend/src/pages/Signup.jsx")
    assert 'localStorage.setItem("aurem_just_logged_in", "1")' in src
