"""
Iter 212m-76 — Tests for the dual-backend admin analytics cache.

Verifies:
  1. In-memory fallback when REDIS_URL is unset (legacy path).
  2. Single-flight semantics — builder runs once even with concurrent
     callers.
  3. TTL expiry behaviour.
  4. invalidate() drops local mirror.
  5. stats() reports redis configured/connected flags.
  6. Cache backend doesn't blow up when Redis is configured but down.
"""
import asyncio
import os
import pytest

import services.admin_analytics_cache as cache


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    cache._reset_for_tests()
    yield
    cache._reset_for_tests()


@pytest.mark.asyncio
async def test_in_memory_fallback_when_no_redis_url():
    calls = {"n": 0}
    async def builder():
        calls["n"] += 1
        return {"value": 42}
    a = await cache.cached_agg("k1", ttl=10, builder=builder)
    b = await cache.cached_agg("k1", ttl=10, builder=builder)
    assert a == b == {"value": 42}
    assert calls["n"] == 1   # builder called once → cache hit on 2nd call


@pytest.mark.asyncio
async def test_single_flight_concurrent_callers():
    calls = {"n": 0}
    started = asyncio.Event()
    async def builder():
        calls["n"] += 1
        started.set()
        await asyncio.sleep(0.05)
        return calls["n"]
    results = await asyncio.gather(
        cache.cached_agg("k2", ttl=10, builder=builder),
        cache.cached_agg("k2", ttl=10, builder=builder),
        cache.cached_agg("k2", ttl=10, builder=builder),
    )
    assert results == [1, 1, 1]
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_ttl_expiry_in_memory():
    calls = {"n": 0}
    async def builder():
        calls["n"] += 1
        return calls["n"]
    a = await cache.cached_agg("k3", ttl=1, builder=builder)
    # Mutate the expiry directly to simulate ttl elapse without a sleep.
    cache._STORE["k3"] = (-1, a)
    b = await cache.cached_agg("k3", ttl=1, builder=builder)
    assert a == 1 and b == 2
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_invalidate_clears_local():
    async def builder():
        return {"v": "x"}
    await cache.cached_agg("k4", ttl=60, builder=builder)
    assert "k4" in cache._STORE
    removed = cache.invalidate("k4")
    assert removed == 1
    assert "k4" not in cache._STORE
    # Invalidating all
    await cache.cached_agg("k5", ttl=60, builder=builder)
    await cache.cached_agg("k6", ttl=60, builder=builder)
    n = cache.invalidate(None)
    assert n == 2
    assert cache._STORE == {}


@pytest.mark.asyncio
async def test_stats_shape():
    async def builder():
        return [1, 2, 3]
    await cache.cached_agg("sk", ttl=60, builder=builder)
    s = cache.stats()
    assert s["entries"] == 1
    assert s["fresh"] == 1
    assert s["stale"] == 0
    assert "sk" in s["keys_sample"]
    assert "redis" in s
    assert s["redis"]["configured"] is False
    assert s["redis"]["connected"] is False


@pytest.mark.asyncio
async def test_invalid_redis_url_falls_back_silently(monkeypatch):
    """When REDIS_URL points to an unreachable host the cache must
    transparently fall back to in-memory instead of raising."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1")  # unreachable
    cache._reset_for_tests()
    calls = {"n": 0}
    async def builder():
        calls["n"] += 1
        return "ok"
    out = await cache.cached_agg("k_redis_down", ttl=5, builder=builder)
    assert out == "ok"
    # Second call should hit the in-mem mirror
    out2 = await cache.cached_agg("k_redis_down", ttl=5, builder=builder)
    assert out2 == "ok"
    assert calls["n"] == 1
    s = cache.stats()
    assert s["redis"]["configured"] is True
    assert s["redis"]["connected"] is False


@pytest.mark.asyncio
async def test_builder_exception_does_not_cache():
    async def boom():
        raise RuntimeError("aggregation broke")
    with pytest.raises(RuntimeError):
        await cache.cached_agg("k_boom", ttl=60, builder=boom)
    assert "k_boom" not in cache._STORE
