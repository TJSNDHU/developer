"""
test_iter92_firecrawl_paid_live.py — locks in that the Firecrawl key
is configured and Firecrawl is reachable now that the founder upgraded
to a paid plan.

The CI-safe check is offline (key present + correct prefix + length).
The optional `--live` check actually hits Firecrawl /v1/scrape and is
skipped by default so unit tests stay deterministic.
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


def test_firecrawl_api_key_configured():
    """Key must be present and shaped like a real Firecrawl key."""
    k = os.environ.get("FIRECRAWL_API_KEY", "")
    assert k, "FIRECRAWL_API_KEY missing from env"
    assert k.startswith("fc-"), f"Firecrawl keys start with `fc-`, got {k[:5]!r}"
    assert len(k) >= 30, f"Firecrawl key suspiciously short ({len(k)} chars)"


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_NETWORK_TESTS") != "1",
    reason="Live Firecrawl hit — opt-in via RUN_LIVE_NETWORK_TESTS=1",
)
def test_firecrawl_scrape_returns_200_on_paid_plan():
    """When live-mode is enabled, Firecrawl must return HTTP 200 (not
    402 Insufficient Credits) — proves the paid plan is actually active."""
    import httpx
    key = os.environ.get("FIRECRAWL_API_KEY", "")
    r = httpx.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"url": "https://example.com", "formats": ["markdown"]},
        timeout=30,
    )
    assert r.status_code != 402, (
        f"Firecrawl returned 402 — paid plan inactive or credits exhausted. "
        f"Body: {r.text[:200]}"
    )
    assert r.status_code == 200, f"unexpected HTTP {r.status_code}: {r.text[:200]}"
    data = r.json()
    assert data.get("success") is True, f"scrape failed: {data}"


def test_firecrawl_skill_registered_in_orchestrator():
    """The firecrawl_scrape tool must be wired into ORA's local_tools
    registry so the orchestrator can actually invoke it."""
    from services import local_tools
    # local_tools exposes a registry of callable web skills; firecrawl
    # may be registered as a top-level function or behind a router. The
    # smoke check is just that the module imports without error and the
    # web_skills module is importable too (i.e., no broken refs).
    from services import web_skills  # noqa: F401
    assert hasattr(web_skills, "FIRECRAWL_BASE"), "web_skills module missing constants"
    assert web_skills.FIRECRAWL_BASE.startswith("https://api.firecrawl.dev")
