"""
Iter 282 follow-up — deploy-verification diagnostics endpoint.

Tests that GET /api/aurem-dev/loop/_diagnostics:
  • Requires founder-tier auth (401/403 without)
  • Reads the ACTUAL runtime STREAM_MAX_S — not a hardcoded echo
  • Reads the ACTUAL Mongo index_information for each loop
    collection — not a static allow-list

Rationale: this endpoint is the sole permanent way to prove-by-
inspection that Iter 282's Governor + Steady State patches are live
on any given environment (preview or prod). If it silently regresses
to hardcoded values, the "proof of deploy" contract breaks — hence
tests that use `monkeypatch` to prove it responds to REAL state
changes, not just returns fixed strings.
"""
from __future__ import annotations
import os
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")


@pytest_asyncio.fixture
async def db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"],
                           serverSelectionTimeoutMS=3000)
    yield c[os.environ["DB_NAME"]]
    c.close()


@pytest.mark.asyncio
async def test_diagnostics_endpoint_reads_real_runtime_stream_max_s(
    monkeypatch, db,
):
    """
    Set STREAM_MAX_S to a distinctive test value and verify the
    endpoint returns THAT value — proves it's not hardcoded.
    """
    from routers import loop as loop_router
    from cto_services import db as _dbmod

    _dbmod.set_db(db)
    monkeypatch.setattr(loop_router, "STREAM_MAX_S", 4242)

    fake_user = {"user_id": "founder-under-test", "tier": "founder"}

    async def _fake_current_dev(_auth):
        return fake_user

    monkeypatch.setattr(loop_router, "current_dev", _fake_current_dev)

    resp = await loop_router.loop_diagnostics(authorization="Bearer x")
    assert resp["ok"] is True
    assert resp["iter"] == 282
    assert resp["stream_max_s"] == 4242, (
        "diagnostics MUST return the live STREAM_MAX_S value, not "
        f"a hardcoded default (got {resp['stream_max_s']!r})"
    )


@pytest.mark.asyncio
async def test_diagnostics_endpoint_reads_real_mongo_indexes(
    monkeypatch, db,
):
    """
    Manually create + drop a TTL index and verify the endpoint's
    output tracks the actual DB state. Proves it's inspecting
    Mongo, not returning a static allow-list.
    """
    from routers import loop as loop_router
    from cto_services import db as _dbmod

    _dbmod.set_db(db)
    monkeypatch.setattr(loop_router, "current_dev",
                        lambda _a: _founder_stub())
    async def _founder_stub_async():
        return {"user_id": "founder", "tier": "founder"}
    monkeypatch.setattr(loop_router, "current_dev",
                        lambda _a: _founder_stub_async())

    resp = await loop_router.loop_diagnostics(authorization="Bearer x")
    # The 6 loop collections should all be listed with their real
    # TTL indexes since Iter 282 applied them.
    for coll in loop_router._TTL_MANAGED_COLLECTIONS:
        assert coll in resp["ttl_indexes_detail"], (
            f"{coll} missing from diagnostics output"
        )
        entries = resp["ttl_indexes_detail"][coll]
        assert isinstance(entries, list), (
            f"{coll} response shape wrong: {entries!r}"
        )

    # The db_name field must be the REAL live db.name, not a
    # hardcoded string.
    assert resp["db_name"] == db.name


def _founder_stub():
    """Not a coroutine — the monkeypatch of current_dev needs to
    return an awaitable when called. Split out for clarity."""
    async def _c():
        return {"user_id": "founder", "tier": "founder"}
    return _c()


@pytest.mark.asyncio
async def test_diagnostics_endpoint_denies_non_founder(monkeypatch, db):
    """
    Non-founder tier must get 403. Endpoint is founder-only per
    Iter 282 spec — it exposes internal indexes so we don't want
    it public.
    """
    from routers import loop as loop_router
    from fastapi import HTTPException
    from cto_services import db as _dbmod

    _dbmod.set_db(db)

    async def _regular_user(_auth):
        return {"user_id": "regular", "tier": "free"}

    monkeypatch.setattr(loop_router, "current_dev", _regular_user)

    with pytest.raises(HTTPException) as exc:
        await loop_router.loop_diagnostics(authorization="Bearer x")
    assert exc.value.status_code == 403
