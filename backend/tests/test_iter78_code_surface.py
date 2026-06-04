"""
test_iter78_code_surface.py — GET /admin/code-surface (Emergent suggestion).

Architecture page now fetches a live file map instead of the hand-rolled
constant. We lock:
  1. Endpoint requires admin.
  2. It returns the four expected categories with non-empty file lists.
  3. The file rows carry `file`, `lines`, `path` keys.
  4. The Architecture page wires `api.get("/admin/code-surface")` and
     keeps the static CODE_SURFACE constant as the offline fallback.
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest

API = "http://localhost:8001/api/aurem-dev"
FOUNDER_EMAIL = "teji.ss1986@gmail.com"
FOUNDER_PASSWORD = "founder-test-pass-9281"


async def _founder_token() -> str:
    async with httpx.AsyncClient(timeout=10.0) as c:
        # Try login first; signup if missing.
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
async def test_code_surface_requires_admin():
    """A brand-new free-tier user should not be able to read it."""
    email = f"u_{uuid.uuid4().hex[:8]}@aurem.test"
    async with httpx.AsyncClient(timeout=10.0) as c:
        s = await c.post(f"{API}/auth/signup", json={
            "email": email, "password": "x" * 12, "name": "Free",
        })
        assert s.status_code == 200, s.text
        r = await c.get(f"{API}/admin/code-surface",
                        headers={"Authorization": f"Bearer {s.json()['token']}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_code_surface_returns_live_file_map():
    token = await _founder_token()
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{API}/admin/code-surface",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    surface = body["surface"]
    for cat in ("routers", "services", "pages", "components"):
        assert cat in surface, f"missing category: {cat}"
        assert len(surface[cat]) > 0, f"empty file list for {cat}"
        sample = surface[cat][0]
        for key in ("file", "lines", "path"):
            assert key in sample, f"{cat} row missing '{key}': {sample}"
        assert sample["lines"] >= 0
    assert body["total_files"] == sum(len(v) for v in surface.values())
    # Specific files we know must exist
    routers = {row["file"] for row in surface["routers"]}
    assert "cto_projects.py" in routers
    assert "automations.py" in routers
    pages = {row["file"] for row in surface["pages"]}
    assert "Automations.jsx" in pages or "Dashboard.jsx" in pages


def test_architecture_page_wires_live_endpoint():
    """The Architecture tab fetches /admin/code-surface on mount and
    still keeps CODE_SURFACE as the offline fallback."""
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, "frontend/src/pages/Admin.jsx"),
              encoding="utf-8") as fh:
        src = fh.read()
    assert '"/admin/code-surface"' in src or "'/admin/code-surface'" in src
    assert "CodeSurfaceLive" in src
    # Static map kept as fallback so the page never bricks if API fails.
    assert "CODE_SURFACE" in src
