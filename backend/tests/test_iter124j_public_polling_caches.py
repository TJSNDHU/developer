"""
Iter 124j — Cache the two hot public-polling endpoints that were driving
Atlas count_documents load past the K8s liveness budget (production
crash loop on a ~42-min cadence).

Endpoints:
  /usage/public/stats — 60s cache (was 6× count_documents/call)
  /wall/feed?limit=N — 30s cache per limit (was aggregate + $lookup + count_documents/call)
"""
from __future__ import annotations

import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest


def _ensure_path():
    sys.path.insert(0, "/app/backend")


# ── public_stats cache ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_public_stats_uses_cache_on_second_call(monkeypatch):
    _ensure_path()
    import routers.usage as u
    # Reset cache between tests
    u._PUBLIC_STATS_CACHE.update({"ts": 0.0, "data": None})

    counts = {"calls": 0}
    fake_db = MagicMock()

    async def _est_count():
        counts["calls"] += 1
        return 100

    async def _filt_count(_q):
        counts["calls"] += 1
        return 5

    fake_db.ora_council_logs.estimated_document_count = _est_count
    fake_db.dev_users.estimated_document_count = _est_count
    fake_db.ora_council_logs.count_documents = _filt_count
    fake_db.cto_tasks.count_documents = _filt_count

    monkeypatch.setattr("cto_services.db.get_db", lambda: fake_db)

    r1 = await u.public_stats()
    r2 = await u.public_stats()
    assert r1["available"] is True
    assert r2 == r1
    # 6 db calls on the first hit (2 estimated + 4 filtered), 0 on the cached call
    assert counts["calls"] == 6


@pytest.mark.asyncio
async def test_public_stats_cache_expires(monkeypatch):
    _ensure_path()
    import routers.usage as u
    # Seed an expired cache
    u._PUBLIC_STATS_CACHE.update({"ts": time.time() - 999, "data": {"stale": True}})

    counts = {"calls": 0}
    fake_db = MagicMock()

    async def _est_count():
        counts["calls"] += 1
        return 1

    async def _filt_count(_q):
        counts["calls"] += 1
        return 1

    fake_db.ora_council_logs.estimated_document_count = _est_count
    fake_db.dev_users.estimated_document_count = _est_count
    fake_db.ora_council_logs.count_documents = _filt_count
    fake_db.cto_tasks.count_documents = _filt_count
    monkeypatch.setattr("cto_services.db.get_db", lambda: fake_db)

    r = await u.public_stats()
    assert r.get("stale") is not True
    assert counts["calls"] == 6


# ── /wall/feed cache ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wall_feed_uses_cache(monkeypatch):
    _ensure_path()
    import routers.shipwall as sw
    sw._FEED_CACHE.clear()

    calls = {"agg": 0, "count": 0}

    class FakeCursor:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration

    def _agg(_pipeline):
        calls["agg"] += 1
        return FakeCursor()

    async def _count(_q):
        calls["count"] += 1
        return 0

    fake_db = MagicMock()
    fake_db.cto_tasks.aggregate = _agg
    fake_db.cto_tasks.count_documents = _count
    monkeypatch.setattr("routers.shipwall.get_db", lambda: fake_db)

    r1 = await sw.ship_wall_feed(limit=50)
    r2 = await sw.ship_wall_feed(limit=50)
    assert r1 == r2
    # First call hits Mongo; second is served from cache
    assert calls["agg"] == 1
    assert calls["count"] == 1


def test_caches_have_sensible_ttls():
    _ensure_path()
    import routers.usage as u, routers.shipwall as sw
    assert u._PUBLIC_STATS_TTL_S >= 30
    assert sw._FEED_TTL_S >= 15
