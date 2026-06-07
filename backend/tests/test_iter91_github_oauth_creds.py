"""
test_iter91_github_oauth_creds.py — locks in the real GitHub App
credentials and the auth-URL builder so the OAuth/login flow is wired
end-to-end.

Live check (founder confirmed):
  • Client ID:     Ov23lisSGhPaNzs6rt2k  (GitHub App format)
  • Client Secret: 2eb0d6a8…0217         (40-char hex)
  • Redirect URI:  https://auremcto.com/api/aurem-dev/github/oauth/callback

We don't network-call GitHub here. We assert:
  • .env has non-empty client_id / client_secret / redirect_uri
  • The client_id matches the real `Ov23li…` prefix (rejects empty /
    placeholder regression).
  • `auth_url(state)` produces a valid github.com authorize URL with
    every required parameter present and the real client_id baked in.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@pytest.fixture(autouse=True)
def _load_env():
    """Force .env to win over any stale process env per-test."""
    load_dotenv(str(ENV_PATH), override=True)
    yield


def test_github_oauth_env_vars_present():
    from services.github_oauth import client_id, client_secret, redirect_uri
    cid = client_id()
    csec = client_secret()
    ruri = redirect_uri()
    assert cid, "GITHUB_OAUTH_CLIENT_ID is empty"
    assert csec, "GITHUB_OAUTH_CLIENT_SECRET is empty"
    assert ruri, "GITHUB_REDIRECT_URI is empty"
    # Real-app shape — rejects empty regression & wrong-app-type if
    # founder ever rotates to a non-GitHub-App format that doesn't
    # match `Ov23li…` and isn't a 20-char hex OAuth ID.
    assert cid.startswith("Ov23li") or len(cid) == 20, (
        f"client_id={cid!r} does not look like a real GitHub App "
        f"(Ov23li…) or OAuth App (20-char hex) credential"
    )
    assert len(csec) == 40, f"GitHub OAuth secrets are 40 hex chars, got {len(csec)}"


def test_redirect_uri_points_to_production_callback():
    from services.github_oauth import redirect_uri
    ruri = redirect_uri()
    parsed = urlparse(ruri)
    assert parsed.scheme == "https", f"redirect_uri must be https, got {ruri!r}"
    assert parsed.path.endswith("/github/oauth/callback"), (
        f"redirect_uri path must end with /github/oauth/callback, got {parsed.path!r}"
    )


def test_auth_url_builds_with_real_client_id():
    from services.github_oauth import auth_url, client_id
    url = auth_url("signup:abc123def")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "github.com"
    assert parsed.path == "/login/oauth/authorize"

    qs = parse_qs(parsed.query)
    assert qs["client_id"][0] == client_id(), "auth_url must embed the real client_id"
    assert qs["state"][0] == "signup:abc123def"
    assert "redirect_uri" in qs and qs["redirect_uri"][0].startswith("https://")
    # We deliberately keep scope in the URL even though GitHub Apps
    # ignore it (OAuth Apps still need it). Just assert it's non-empty.
    assert qs.get("scope", [""])[0], "scope param missing from auth_url"
