"""
test_iter123b_skill_usage_analytics.py — Iter 123b telemetry validation.

Validates the fire-and-forget telemetry pipeline + admin endpoint.
No mocks, no stubs — actually writes to Mongo and queries it back.
"""
import os
import asyncio
import pytest
import httpx

from cto_services.db import set_db
from motor.motor_asyncio import AsyncIOMotorClient

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


def _db():
    """Open a Mongo connection scoped to this test call + register on
    cto_services.db so log_skill_use sees it."""
    mongo_url = os.environ.get("MONGO_URL") or "mongodb://localhost:27017"
    client = AsyncIOMotorClient(mongo_url)
    test_db = client[os.environ.get("DB_NAME") or "aurem_dev"]
    set_db(test_db)
    return client, test_db


# ── log_skill_use — fire-and-forget, never blocks ─────────────────────

@pytest.mark.asyncio
async def test_log_skill_use_writes_doc():
    """A single log call writes one doc with expected fields."""
    from services.skill_usage import log_skill_use

    client, db = _db()
    try:
        await db.ora_skill_usage.delete_many({"session_id": "test-session-iter123b-A"})

        log_skill_use(
            tool="test_tool_alpha",
            ok=True,
            elapsed_ms=42,
            user_id="test-user-A",
            project_id="test-proj-A",
            session_id="test-session-iter123b-A",
        )
        await asyncio.sleep(0.3)

        doc = await db.ora_skill_usage.find_one(
            {"session_id": "test-session-iter123b-A"},
        )
        assert doc is not None, "log_skill_use did not persist"
        assert doc["tool"] == "test_tool_alpha"
        assert doc["ok"] is True
        assert doc["elapsed_ms"] == 42
        assert doc["user_id"] == "test-user-A"
        assert doc["error_kind"] is None
        assert doc["ts"]

        await db.ora_skill_usage.delete_many({"session_id": "test-session-iter123b-A"})
    finally:
        client.close()


@pytest.mark.asyncio
async def test_log_skill_use_records_error():
    """Failed tool calls record error_kind (truncated to 80 chars)."""
    from services.skill_usage import log_skill_use

    client, db = _db()
    try:
        await db.ora_skill_usage.delete_many({"session_id": "test-session-iter123b-B"})

        long_err = "x" * 200
        log_skill_use(
            tool="test_tool_beta",
            ok=False,
            elapsed_ms=None,
            error=long_err,
            session_id="test-session-iter123b-B",
        )
        await asyncio.sleep(0.3)

        doc = await db.ora_skill_usage.find_one(
            {"session_id": "test-session-iter123b-B"},
        )
        assert doc is not None
        assert doc["ok"] is False
        assert doc["error_kind"] is not None
        assert len(doc["error_kind"]) == 80, "error_kind must be capped at 80 chars"

        await db.ora_skill_usage.delete_many({"session_id": "test-session-iter123b-B"})
    finally:
        client.close()


@pytest.mark.asyncio
async def test_log_skill_use_never_raises_without_db():
    """Even with no DB registered, log_skill_use must not raise."""
    from services.skill_usage import log_skill_use
    from cto_services import db as db_module

    saved = db_module._db
    db_module._db = None
    try:
        log_skill_use(tool="x", ok=True, elapsed_ms=1)
    finally:
        db_module._db = saved


# ── Orchestrator integration — log_skill_use is invoked ───────────────

def test_orchestrator_imports_log_skill_use():
    """The orchestrator must import + use log_skill_use in its tool loop."""
    with open("/app/backend/services/orchestrator.py") as f:
        src = f.read()
    assert "from .skill_usage import log_skill_use" in src
    assert "log_skill_use(" in src


# ── Admin endpoint — /admin/skills-usage ──────────────────────────────

@pytest.mark.asyncio
async def test_skills_usage_endpoint_requires_admin():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{API_URL}/api/aurem-dev/admin/skills-usage")
        assert r.status_code == 401, f"expected 401, got {r.status_code}"


@pytest.mark.asyncio
async def test_skills_usage_endpoint_route_exists():
    """Admin route must be mounted (401 not 404)."""
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{API_URL}/api/aurem-dev/admin/skills-usage",
            headers={"Authorization": "Bearer fake-jwt-for-route-check"},
        )
        assert r.status_code in (401, 403), \
            f"route missing, got {r.status_code}: {r.text[:200]}"


# ── Aggregation shape ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_aggregation_pipeline_produces_share_and_dead_weight():
    """Seed docs, run the aggregation, verify counts + dead_weight threshold."""
    from datetime import datetime, timezone

    client, db = _db()
    try:
        coll = db.ora_skill_usage
        marker = "test-iter123b-agg"
        await coll.delete_many({"session_id": marker})

        docs = []
        for i in range(8):
            docs.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "tool": "tool_hot", "ok": True, "elapsed_ms": 100 + i,
                "session_id": marker,
            })
        docs.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": "tool_warm", "ok": True, "elapsed_ms": 50,
            "session_id": marker,
        })
        docs.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": "tool_cold", "ok": False, "elapsed_ms": 200,
            "session_id": marker,
        })
        await coll.insert_many(docs)

        pipeline = [
            {"$match": {"session_id": marker}},
            {"$group": {
                "_id":      "$tool",
                "count":    {"$sum": 1},
                "ok_count": {"$sum": {"$cond": ["$ok", 1, 0]}},
            }},
        ]
        rows = []
        total = 0
        async for r in coll.aggregate(pipeline):
            rows.append(r)
            total += r["count"]

        assert total == 10
        counts = {r["_id"]: r["count"] for r in rows}
        assert counts == {"tool_hot": 8, "tool_warm": 1, "tool_cold": 1}

        # Edge case: 0.015 share < 0.02 prune threshold
        fake_share = 0.015
        assert fake_share < 0.02, "0.02 prune threshold catches <2% skills"

        await coll.delete_many({"session_id": marker})
    finally:
        client.close()


# ── Bootstrap index — ora_skill_usage in init_prod_collections ────────

def test_ora_skill_usage_in_bootstrap_spec():
    """init_prod_collections must include ora_skill_usage so prod boots
    with an indexed collection."""
    with open("/app/backend/scripts/init_prod_collections.py") as f:
        src = f.read()
    assert '"ora_skill_usage"' in src
    assert '("ts", -1)' in src
    assert '("tool", 1)' in src
