"""
test_iter97_vercel_api_token.py — locks in the Vercel API token.

The token is used by:
  • Admin "env health" panel — exposes `vercel_deploy_hook: True`
    when configured.
  • Future hosted-deploy flow (`routers/hosted_deploy.py`) — for one-
    click "Deploy to Vercel" from chat once the customer has linked
    their Vercel account.

Offline checks always run. Live (opt-in via RUN_LIVE_NETWORK_TESTS=1)
hits Vercel's `/v2/user` endpoint to prove the token is valid.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@pytest.fixture(autouse=True)
def _load_env():
    load_dotenv(str(ENV_PATH), override=True)
    yield


def test_vercel_api_token_configured():
    """Token must be present and shaped like a real Vercel personal token."""
    k = os.environ.get("VERCEL_API_TOKEN", "")
    assert k, "VERCEL_API_TOKEN missing from env"
    # Vercel's new personal-token format is `vcp_…` (older was 24-char hex).
    # Accept either format so future rotations don't break this test.
    valid_shape = k.startswith("vcp_") or (len(k) == 24 and all(c in "0123456789abcdef" for c in k.lower()))
    assert valid_shape, f"VERCEL_API_TOKEN shape not recognised: {k[:6]}... (len={len(k)})"
    assert len(k) >= 24, f"VERCEL_API_TOKEN suspiciously short ({len(k)} chars)"


def test_admin_health_flag_reads_vercel_env():
    """The admin env-health panel must surface `vercel_deploy_hook=True`
    when the token is configured — that's how the founder knows the
    integration is wired."""
    src = (Path(__file__).resolve().parents[1] / "routers" / "admin.py").read_text()
    assert 'os.getenv("VERCEL_API_TOKEN")' in src, (
        "admin.py must check VERCEL_API_TOKEN env for the health flag"
    )


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_NETWORK_TESTS") != "1",
    reason="Live Vercel API hit — opt-in via RUN_LIVE_NETWORK_TESTS=1",
)
def test_vercel_token_is_valid_against_live_api():
    """Confirm the token actually authenticates against Vercel's API.
    Hits `/v2/user` which Vercel docs designate as the canonical
    token-validation endpoint."""
    import httpx
    key = os.environ.get("VERCEL_API_TOKEN", "")
    r = httpx.get(
        "https://api.vercel.com/v2/user",
        headers={"Authorization": f"Bearer {key}"},
        timeout=10,
    )
    assert r.status_code == 200, (
        f"Vercel rejected token: HTTP {r.status_code} {r.text[:200]}"
    )
    data = r.json().get("user", {})
    assert data.get("email"), f"Vercel /v2/user returned no email: {data}"
