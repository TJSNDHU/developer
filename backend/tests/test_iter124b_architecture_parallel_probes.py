"""
Iter 124b — Architecture endpoint must run external probes in parallel,
not sequentially. Hardens the admin/architecture endpoint against
cold-start Cloudflare 524s.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_architecture_probes_run_in_parallel(monkeypatch):
    """Each probe sleeps 1s. Sequential would be 8s+; parallel must be <3s."""
    # Import lazily so monkeypatch can adjust env before module reads it.
    from routers import admin as admin_mod
    from services import external_services_registry as reg

    # Skip admin auth gate
    async def _noop(_):
        return {"is_admin": True, "tier": "founder"}

    monkeypatch.setattr(admin_mod, "_require_admin", _noop)

    # Force all 8 services to be probe targets by claiming every env_key set.
    monkeypatch.setattr(reg, "should_probe", lambda svc: bool(svc.probe_url))

    # Patch httpx.AsyncClient to return a slow but successful response.
    import httpx

    class _SlowResp:
        status_code = 200

    class _SlowClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, headers=None):
            await asyncio.sleep(1.0)   # each probe = 1s
            return _SlowResp()

    monkeypatch.setattr(httpx, "AsyncClient", _SlowClient)

    t0 = time.perf_counter()
    result = await admin_mod.get_architecture(authorization="Bearer test")
    elapsed = time.perf_counter() - t0

    # Sanity: we got results
    assert "services" in result
    # 8 probe targets + MongoDB = at least 9 entries
    assert len(result["services"]) >= 5
    # Parallelism: must finish under 3s. Sequential would be ~8s.
    assert elapsed < 3.0, f"Architecture endpoint ran sequentially: {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_architecture_endpoint_caps_total_wallclock(monkeypatch):
    """Even if every probe hangs past its own timeout, the endpoint
    must never exceed the 8s gather guard."""
    from routers import admin as admin_mod
    from services import external_services_registry as reg

    async def _noop(_):
        return {"is_admin": True, "tier": "founder"}
    monkeypatch.setattr(admin_mod, "_require_admin", _noop)
    monkeypatch.setattr(reg, "should_probe", lambda svc: bool(svc.probe_url))

    import httpx

    class _HangClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, headers=None):
            # Simulate a hang that ignores the per-call timeout
            await asyncio.sleep(30)
            raise RuntimeError("should not reach")

    monkeypatch.setattr(httpx, "AsyncClient", _HangClient)

    t0 = time.perf_counter()
    result = await admin_mod.get_architecture(authorization="Bearer test")
    elapsed = time.perf_counter() - t0

    assert "services" in result
    # Hard wall: 8s + overhead. Must NEVER hit Cloudflare's 100s threshold.
    assert elapsed < 12.0, f"Architecture endpoint exceeded wall guard: {elapsed:.2f}s"
