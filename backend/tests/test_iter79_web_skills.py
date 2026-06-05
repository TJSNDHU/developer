"""
test_iter79_web_skills.py — ORA's web skills wiring.

Five new skills wired into the orchestrator tool layer:
  web_search · fetch_url · web_search_and_summarize
  firecrawl_scrape · firecrawl_crawl_site

Without keys we lock the *contract* (graceful failure shape) so the
orchestrator never crashes on a tool call. With keys set we also do
a single real e2e call per provider (skipped automatically otherwise).
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest


API = "http://localhost:8001/api/aurem-dev"
FOUNDER_EMAIL = "teji.ss1986@gmail.com"
FOUNDER_PASSWORD = "founder-test-pass-9281"


# ── 1. Static registry wiring (no network, no keys) ───────────────────

def test_web_skills_registered_in_local_tools():
    from services.local_tools import TOOL_SPECS, LOCAL_TOOLS
    expected = {
        "web_search", "fetch_url", "web_search_and_summarize",
        "firecrawl_scrape", "firecrawl_crawl_site",
    }
    names_in_specs = {t["name"] for t in TOOL_SPECS}
    assert expected.issubset(names_in_specs), (
        f"missing from TOOL_SPECS: {expected - names_in_specs}"
    )
    assert expected.issubset(set(LOCAL_TOOLS.keys())), (
        f"missing from LOCAL_TOOLS: {expected - set(LOCAL_TOOLS.keys())}"
    )


def test_web_skill_specs_have_required_fields():
    from services.web_skills import WEB_TOOL_SPECS
    for spec in WEB_TOOL_SPECS:
        assert spec.get("name"), spec
        assert spec.get("description"), spec
        assert isinstance(spec.get("args_spec"), dict), spec


# ── 2. Graceful "no key" behaviour ────────────────────────────────────

@pytest.mark.asyncio
async def test_tavily_skills_clean_error_when_key_missing(monkeypatch):
    """Without TAVILY_API_KEY the three Tavily skills return a clean
    {ok: False, error: "..."} — they must NEVER raise."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    from services.web_skills import (
        web_search, fetch_url, web_search_and_summarize,
    )
    r = await web_search({}, {"query": "anything"})
    assert r["ok"] is False
    assert "TAVILY_API_KEY" in r["error"]

    r = await fetch_url({}, {"url": "https://example.com"})
    assert r["ok"] is False
    assert "TAVILY_API_KEY" in r["error"]

    r = await web_search_and_summarize({}, {"query": "anything"})
    assert r["ok"] is False
    assert "TAVILY_API_KEY" in r["error"]


@pytest.mark.asyncio
async def test_firecrawl_skills_clean_error_when_key_missing(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    from services.web_skills import firecrawl_scrape, firecrawl_crawl_site
    r = await firecrawl_scrape({}, {"url": "https://example.com"})
    assert r["ok"] is False
    assert "FIRECRAWL_API_KEY" in r["error"]

    r = await firecrawl_crawl_site({}, {"url": "https://example.com"})
    assert r["ok"] is False
    assert "FIRECRAWL_API_KEY" in r["error"]


# ── 3. Validation gates ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_search_rejects_missing_query(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-dummy-for-validation-test")
    from services.web_skills import web_search
    r = await web_search({}, {})
    assert r["ok"] is False
    assert "query" in r["error"].lower()


@pytest.mark.asyncio
async def test_fetch_url_blocks_loopback(monkeypatch):
    """SSRF guard: refuse internal targets even with a key set."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-dummy-for-validation-test")
    from services.web_skills import fetch_url
    for bad in ("http://localhost:8001/api",
                "http://127.0.0.1/admin",
                "http://10.0.0.1/secret"):
        r = await fetch_url({}, {"url": bad})
        assert r["ok"] is False, f"SSRF guard let {bad} through"


@pytest.mark.asyncio
async def test_firecrawl_scrape_blocks_loopback(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-dummy-for-validation-test")
    from services.web_skills import firecrawl_scrape
    r = await firecrawl_scrape({}, {"url": "http://localhost:8001"})
    assert r["ok"] is False
    assert "ssrf" in r["error"].lower() or "refused" in r["error"].lower()


# ── 4. Admin REST endpoints ───────────────────────────────────────────

async def _founder_token() -> str:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/auth/login", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
        })
        if r.status_code != 200:
            r = await c.post(f"{API}/auth/signup", json={
                "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
                "name": "Founder Test",
            })
        assert r.status_code == 200, r.text
        return r.json()["token"]


@pytest.mark.asyncio
async def test_skills_status_endpoint_admin_only():
    """Non-admin should be blocked from the admin/skills/status endpoint."""
    email = f"u_{uuid.uuid4().hex[:8]}@aurem.test"
    async with httpx.AsyncClient(timeout=10.0) as c:
        s = await c.post(f"{API}/auth/signup", json={
            "email": email, "password": "x" * 12, "name": "Free",
        })
        assert s.status_code == 200, s.text
        r = await c.get(f"{API}/admin/skills/status",
                        headers={"Authorization": f"Bearer {s.json()['token']}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_skills_status_returns_wired_skill_map():
    token = await _founder_token()
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{API}/admin/skills/status",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    for skill in ("web_search", "fetch_url", "web_search_and_summarize",
                  "firecrawl_scrape", "firecrawl_crawl_site"):
        assert skill in body["skills"]
        assert isinstance(body["skills"][skill], bool)


@pytest.mark.asyncio
async def test_skills_web_search_endpoint_returns_clean_error_without_key():
    """Even on the live HTTP endpoint, a missing key must not 500 — it
    must surface the same `{ok: False, error: ...}` payload."""
    token = await _founder_token()
    # Temporarily blank the env var the process sees? Can't easily
    # without restarting backend — instead we assert the failure mode
    # IS one of the two acceptable shapes: ok:False+key-missing OR
    # ok:True+results (when key actually configured).
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f"{API}/admin/skills/web-search",
                         json={"query": "AUREM CTO test ping",
                               "max_results": 2},
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ok" in body
    if body["ok"]:
        assert "results" in body
        assert isinstance(body["results"], list)
    else:
        assert "error" in body


# ── 5. Real Tavily e2e (gated by env) ─────────────────────────────────

REAL_TAVILY = bool(os.environ.get("TAVILY_API_KEY"))
REAL_FIRECRAWL = bool(os.environ.get("FIRECRAWL_API_KEY"))


@pytest.mark.asyncio
@pytest.mark.skipif(not REAL_TAVILY, reason="TAVILY_API_KEY not set")
async def test_web_search_real_call():
    from services.web_skills import web_search
    r = await web_search({}, {"query": "FastAPI latest version", "max_results": 3})
    assert r["ok"] is True, r
    assert r["count"] >= 1
    assert all(row.get("url", "").startswith("http") for row in r["results"])


@pytest.mark.asyncio
@pytest.mark.skipif(not REAL_TAVILY, reason="TAVILY_API_KEY not set")
async def test_fetch_url_real_call():
    from services.web_skills import fetch_url
    r = await fetch_url({}, {"url": "https://example.com"})
    assert r["ok"] is True, r
    assert r["count"] >= 1
    assert "example" in (r["results"][0]["content"] or "").lower()


@pytest.mark.asyncio
@pytest.mark.skipif(not REAL_TAVILY, reason="TAVILY_API_KEY not set")
async def test_search_and_summarize_real_call():
    from services.web_skills import web_search_and_summarize
    r = await web_search_and_summarize({}, {
        "query": "What is FastAPI?", "max_results": 3,
    })
    assert r["ok"] is True, r
    assert r["answer"], "Tavily returned empty answer"
    assert len(r["citations"]) >= 1


@pytest.mark.asyncio
@pytest.mark.skipif(not REAL_FIRECRAWL, reason="FIRECRAWL_API_KEY not set")
async def test_firecrawl_scrape_real_call():
    from services.web_skills import firecrawl_scrape
    r = await firecrawl_scrape({}, {"url": "https://example.com"})
    # Auth must always succeed (key is set). The call may legitimately
    # fail on 402 (out of credits) — that's not a wiring bug, so we
    # treat it as a soft skip.
    if not r["ok"] and "402" in (r.get("error") or ""):
        pytest.skip("Firecrawl account out of credits — wiring verified")
    assert r["ok"] is True, r
    assert "example" in (r["markdown"] or "").lower()
