"""Iter 113 — GitHub OAuth Cancel from LOGIN page must redirect to /login.

Bug: user reported on production that clicking Cancel during GitHub
sign-in from the /login page showed an error (or sent them to /signup,
not /login). The iter109 fix only redirected `signup:` states to /signup;
all login-originating cancels were lumped in there because both flows
shared the same state prefix.

Fix:
  • `/connect?signup=1&intent=login` → state `login:<nonce>`
  • Cancel handler: state starting with `login:` → /login?github=cancelled
  • Success handler: both `login:` and `signup:` use the OAuth-first
    auth flow identically (no regression to the sign-in/sign-up logic).
"""
import os
import pytest

os.environ.setdefault("APP_URL", "https://auremcto.com")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")

from httpx import AsyncClient, ASGITransport
from main import app  # noqa: E402


@pytest.mark.asyncio
async def test_login_cancel_redirects_to_login():
    """Exact production scenario: user clicked Sign in with GitHub on
    /login, then clicked Cancel on GitHub. Must land on /login with a
    friendly query param — not /signup, not 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/callback",
            params={
                "error": "access_denied",
                "error_description": "The user has denied your application access.",
                "state": "login:abc123",
            },
            follow_redirects=False,
        )
    assert r.status_code in (302, 307), f"expected redirect, got {r.status_code}"
    loc = r.headers.get("location", "")
    assert "/login" in loc, f"expected /login, got {loc!r}"
    assert "github=cancelled" in loc
    assert "/signup" not in loc, "must NOT bounce to /signup from a login-flow cancel"


@pytest.mark.asyncio
async def test_signup_cancel_still_redirects_to_signup():
    """Iter 109 behaviour must still hold for the signup flow — no
    regression from adding the login prefix."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/callback",
            params={
                "error": "access_denied",
                "state": "signup:def456",
            },
            follow_redirects=False,
        )
    loc = r.headers.get("location", "")
    assert "/signup" in loc
    assert "/login" not in loc


@pytest.mark.asyncio
async def test_connect_cancel_still_redirects_to_settings():
    """Existing connect flow (user_id:nonce) must still go to /settings."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/callback",
            params={
                "error": "access_denied",
                "state": "user_abc:nonce_xyz",
            },
            follow_redirects=False,
        )
    loc = r.headers.get("location", "")
    assert "/settings" in loc
    assert "/login" not in loc
    assert "/signup" not in loc


@pytest.mark.asyncio
async def test_login_intent_creates_login_state():
    """The /connect endpoint with intent=login must build a state that
    starts with `login:` so the cancel handler can route correctly."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/connect",
            params={"signup": "1", "intent": "login"},
            follow_redirects=False,
        )
    # Backend redirects to github.com/login/oauth/authorize?...state=login:...
    loc = r.headers.get("location", "")
    assert "github.com" in loc, f"expected redirect to github.com, got {loc!r}"
    assert "state=login%3A" in loc or "state=login:" in loc, \
        f"state did NOT start with 'login:' — got {loc[-200:]}"


@pytest.mark.asyncio
async def test_signup_intent_creates_signup_state_unchanged():
    """No regression: bare `?signup=1` (no intent) still uses `signup:`."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/connect",
            params={"signup": "1"},
            follow_redirects=False,
        )
    loc = r.headers.get("location", "")
    assert "state=signup%3A" in loc or "state=signup:" in loc
