"""Iter 109 — graceful handling when user clicks "Cancel" on GitHub OAuth.

Bug from production logs:
  GET /api/aurem-dev/github/oauth/callback
      ?error=access_denied
      &error_description=The+user+has+denied+your+application+access.
      &state=signup%3A8e3f28ce65114276aec467b8367102f1
  → 422 Unprocessable Entity   (was: code Query(...) required)

After fix: should redirect to /signup?github=cancelled&reason=access_denied
"""
import os
import pytest
from httpx import AsyncClient, ASGITransport

# Ensure APP_URL is set BEFORE the module that reads it is imported via TestClient flow.
os.environ.setdefault("APP_URL", "https://auremcto.com")

from main import app  # noqa: E402


@pytest.mark.asyncio
async def test_callback_user_cancelled_redirects_signup():
    """User clicked Cancel → access_denied → friendly redirect, NOT 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/callback",
            params={
                "error": "access_denied",
                "error_description": "The user has denied your application access.",
                "state": "signup:8e3f28ce65114276aec467b8367102f1",
            },
            follow_redirects=False,
        )
    assert r.status_code in (302, 307), f"expected redirect, got {r.status_code}"
    loc = r.headers.get("location", "")
    assert "/signup" in loc, f"expected /signup redirect, got {loc!r}"
    assert "github=cancelled" in loc
    assert "reason=access_denied" in loc


@pytest.mark.asyncio
async def test_callback_user_cancelled_connect_flow_redirects_settings():
    """Same cancel but during /settings connect flow → redirect to /settings."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/callback",
            params={
                "error": "access_denied",
                "state": "user_abc:nonce_xyz",   # non-signup state
            },
            follow_redirects=False,
        )
    assert r.status_code in (302, 307)
    loc = r.headers.get("location", "")
    assert "/settings" in loc
    assert "github=cancelled" in loc


@pytest.mark.asyncio
async def test_callback_missing_code_without_explicit_error_also_redirects():
    """Edge case: GitHub omits both `code` and `error` (rare but possible).
    We still must NOT 422 — redirect to a friendly URL."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/callback",
            params={"state": "signup:abc123"},
            follow_redirects=False,
        )
    assert r.status_code in (302, 307)
    loc = r.headers.get("location", "")
    assert "github=cancelled" in loc
    assert "reason=missing_code" in loc


@pytest.mark.asyncio
async def test_callback_no_state_no_code_no_error():
    """Totally empty callback — still no 422, redirects to a sensible page."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/callback",
            follow_redirects=False,
        )
    # No 422 — should redirect (defaults to /settings path)
    assert r.status_code in (302, 307), f"got {r.status_code}: {r.text[:200]}"
    assert "github=cancelled" in r.headers.get("location", "")
