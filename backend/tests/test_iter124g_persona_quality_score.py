"""
Iter 124g — Persona Quality Score persistence + admin endpoint.

Asserts:
  1. runner.run() writes a doc into ora_eval_runs (real Mongo).
  2. GET /admin/eval-quality returns the latest run + 30-day trend.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest


async def _ensure_db(force_new: bool = False):
    """Bootstrap Mongo for standalone pytest context. Each pytest-asyncio
    test gets a fresh event loop, so a Motor client cached from a previous
    test will be tied to a dead loop — use force_new=True to rebuild."""
    from cto_services.db import get_db, set_db
    if not force_new:
        db = get_db()
        if db is not None:
            return db
    from motor.motor_asyncio import AsyncIOMotorClient
    url = os.environ.get("MONGO_URL")
    name = os.environ.get("DB_NAME")
    if not url or not name:
        return None
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=3000)
    set_db(client[name])
    return client[name]


@pytest.mark.asyncio
async def test_eval_quality_endpoint_shape(monkeypatch):
    from routers import admin as admin_mod
    db = await _ensure_db()
    if db is None:
        pytest.skip("Mongo not connected in this test environment")

    # Skip admin auth gate
    async def _noop(_):
        return {"is_admin": True, "tier": "founder"}
    monkeypatch.setattr(admin_mod, "_require_admin", _noop)

    # Seed two fake runs (cleanup after)
    suffix = f"itest_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
    now = datetime.now(timezone.utc).isoformat()
    seed = [
        {"ts": now, "total": 42, "passed": 40, "hard_fails": 0,
         "soft_fails": 2, "ok": True, "_seed": suffix},
        {"ts": now, "total": 42, "passed": 38, "hard_fails": 1,
         "soft_fails": 3, "ok": False, "_seed": suffix},
    ]
    await db.ora_eval_runs.insert_many(seed)
    try:
        result = await admin_mod.eval_quality(authorization="Bearer x")
        assert "latest" in result
        assert "trend" in result
        assert "totals" in result
        assert result["totals"]["runs"] >= 2
        # Trend entries should expose score, hard_fails, ts
        for entry in result["trend"][-2:]:
            assert "score" in entry
            assert "hard_fails" in entry
            assert "ts" in entry
    finally:
        await db.ora_eval_runs.delete_many({"_seed": suffix})


@pytest.mark.asyncio
async def test_runner_writes_to_ora_eval_runs(monkeypatch):
    """A run with no LLM key still skips gracefully, so we patch the
    has_llm_key branch to fall through to the persist block."""
    from evals import runner as rmod
    db = await _ensure_db(force_new=True)
    if db is None:
        pytest.skip("Mongo not connected")

    # Stub LLM key + the LLM-touching coroutines so we don't actually
    # spend a call. We want to test ONLY the persistence side-effect.
    monkeypatch.setenv("EMERGENT_LLM_KEY", "test-key")

    async def _fake_run_prompt(spec, budget):
        return {
            "id": spec["id"], "category": spec.get("category", "T"),
            "prompt": spec["prompt"][:80], "elapsed_ms": 1,
            "tool_calls_run": 1, "reply_chars": 10, "reply_preview": "ok",
            "scorers": [],
            "hard_fails": 0, "soft_fails": 0,
        }

    async def _fake_scope():
        return {"id": "S8", "scorers": [], "hard_fails": 0, "soft_fails": 0}

    monkeypatch.setattr(rmod, "_run_prompt", _fake_run_prompt)
    monkeypatch.setattr(rmod, "_project_scoping_isolation_test", _fake_scope)

    before = await db.ora_eval_runs.count_documents({})
    await rmod.run(quick=True)
    after = await db.ora_eval_runs.count_documents({})
    assert after == before + 1
