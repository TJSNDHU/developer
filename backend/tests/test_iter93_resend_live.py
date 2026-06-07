"""
test_iter93_resend_live.py — locks in the Resend API key and confirms
it's a real `re_…` credential pointing at a Resend account with at
least one verified sending domain (`aurem.live`).

Offline checks always run. Live network check is opt-in via
RUN_LIVE_NETWORK_TESTS=1 (mirrors the iter92 Firecrawl test pattern).
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


def test_resend_api_key_configured():
    """Key must be present, prefixed `re_`, and the right length."""
    k = os.environ.get("RESEND_API_KEY", "")
    assert k, "RESEND_API_KEY missing from env"
    assert k.startswith("re_"), f"Resend keys start with `re_`, got {k[:5]!r}"
    assert len(k) >= 30, f"Resend key suspiciously short ({len(k)} chars)"


def test_resend_consumed_by_email_modules():
    """`shared/providers/email_legacy.py` and `services/daily_digest.py`
    must both read RESEND_API_KEY without crashing on module import."""
    # If either of these imports raises, the env wiring is broken.
    from shared.providers import email_legacy  # noqa: F401
    from services import daily_digest          # noqa: F401
    # email_legacy reads the key at import time, so verify the value
    # actually loaded (not just falsy default).
    assert email_legacy._RESEND_KEY == os.environ.get("RESEND_API_KEY"), (
        "email_legacy module captured a different value than env (load order bug)"
    )


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_NETWORK_TESTS") != "1",
    reason="Live Resend hit — opt-in via RUN_LIVE_NETWORK_TESTS=1",
)
def test_resend_account_has_verified_sending_domain():
    """Confirm the account actually has `aurem.live` verified — this is
    the domain we send all production email from (`ora@aurem.live`).
    Without this verified, every send would 422 with `domain not verified`."""
    import httpx
    key = os.environ.get("RESEND_API_KEY", "")
    r = httpx.get(
        "https://api.resend.com/domains",
        headers={"Authorization": f"Bearer {key}"},
        timeout=10,
    )
    assert r.status_code == 200, f"Resend rejected key: {r.status_code} {r.text[:200]}"
    domains = r.json().get("data", [])
    verified = {d.get("name") for d in domains if d.get("status") == "verified"}
    assert "aurem.live" in verified, (
        f"aurem.live is NOT verified on this Resend account — production "
        f"emails from ora@aurem.live will fail. Verified domains: {verified}"
    )


def test_from_addresses_use_verified_domain():
    """Both email pipelines must use the verified `aurem.live` sender."""
    from_email = os.environ.get("RESEND_FROM_EMAIL", "")
    digest_from = os.environ.get("DIGEST_FROM", "")
    assert "ora@aurem.live" in from_email, (
        f"RESEND_FROM_EMAIL must use ora@aurem.live (verified), got {from_email!r}"
    )
    assert "ora@aurem.live" in digest_from, (
        f"DIGEST_FROM must use ora@aurem.live (verified), got {digest_from!r}"
    )
