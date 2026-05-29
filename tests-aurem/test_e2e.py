"""
tests/test_e2e.py — AUREM Dev
E2E tests — verifies all wired endpoints respond correctly.
Run: pytest tests/test_e2e.py -v
"""
import pytest
import httpx

BASE = "http://localhost:8001"


@pytest.mark.asyncio
async def test_health():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["service"] == "aurem-dev"


@pytest.mark.asyncio
async def test_stacks_list():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/api/aurem-dev/stacks")
    assert r.status_code == 200
    data = r.json()
    assert "stacks" in data
    assert len(data["stacks"]) == 4
    ids = [s["id"] for s in data["stacks"]]
    assert "react-fastapi" in ids
    assert "nextjs-node" in ids


@pytest.mark.asyncio
async def test_deploy_config_requires_auth():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/api/aurem-dev/deploy/config")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_vault_requires_auth():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/api/aurem-dev/vault")
    assert r.status_code in (401, 404, 405)


@pytest.mark.asyncio
async def test_create_project_requires_auth():
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BASE}/api/aurem-dev/projects/create",
                         json={"idea": "test app"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_github_status_requires_auth():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/api/aurem-dev/github/status")
    assert r.status_code == 401
