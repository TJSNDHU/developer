"""
Iter 212m-79 — Tests for the cross-pod scan text-cache dedup.

Verifies:
  1. Returns None when REDIS_URL not configured (graceful disable).
  2. Returns None for empty / missing inputs.
  3. Put + get round-trip when Redis available (mocked client).
  4. Skips writes that exceed the 6 MB cap.
  5. Stats shape includes hit/miss + redis flags.
  6. Errors NEVER propagate — caller gets None / False.
"""
import json
import gzip
import pytest

import services.scan_cache as sc


class _FakeRedis:
    """Minimal async Redis stub — just GET/SET/PING for cache testing."""
    def __init__(self): self._db = {}
    async def ping(self): return True
    async def set(self, k, v, ex=None):
        self._db[k] = v
        return True
    async def get(self, k):
        return self._db.get(k)
    async def delete(self, *keys):
        for k in keys: self._db.pop(k, None)
        return len(keys)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    sc._reset_for_tests()
    yield
    sc._reset_for_tests()


@pytest.mark.asyncio
async def test_disabled_when_no_redis_url():
    out = await sc.get_cached_text_cache("o", "r", "sha")
    assert out is None
    ok = await sc.put_cached_text_cache("o", "r", "sha", {"a.py": "x"})
    assert ok is False


@pytest.mark.asyncio
async def test_returns_none_for_empty_inputs(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379")
    sc._CLIENT = _FakeRedis()
    sc._CONNECTED = True
    assert await sc.get_cached_text_cache("", "r", "sha") is None
    assert await sc.get_cached_text_cache("o", "r", "") is None
    assert await sc.put_cached_text_cache("o", "r", "", {"a": "b"}) is False
    assert await sc.put_cached_text_cache("o", "r", "sha", {}) is False


@pytest.mark.asyncio
async def test_round_trip(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379")
    fake = _FakeRedis()
    sc._CLIENT = fake
    sc._CONNECTED = True
    bundle = {"app.py": "print('hi')", "lib/util.js": "export const a = 1;"}
    ok = await sc.put_cached_text_cache("octo", "demo", "deadbeef", bundle)
    assert ok is True
    # Get back
    out = await sc.get_cached_text_cache("octo", "demo", "deadbeef")
    assert out == bundle
    # Wire bytes round-trip through gzip+json
    raw = fake._db["aurem:scan_textcache:octo/demo@deadbeef"]
    decoded = json.loads(gzip.decompress(raw).decode("utf-8"))
    assert decoded == bundle


@pytest.mark.asyncio
async def test_skips_oversized_bundle(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379")
    sc._CLIENT = _FakeRedis()
    sc._CONNECTED = True
    monkeypatch.setattr(sc, "_MAX_ENTRY_BYTES", 100)
    # Build a non-trivial payload that exceeds the cap after gzip.
    big_bundle = {f"f{i}.py": "x" * 1000 for i in range(20)}
    ok = await sc.put_cached_text_cache("o", "r", "sha", big_bundle)
    assert ok is False
    s = sc.get_scan_cache_stats()
    assert s["skipped_too_big"] >= 1


@pytest.mark.asyncio
async def test_get_safe_on_corrupted_value(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379")
    fake = _FakeRedis()
    sc._CLIENT = fake
    sc._CONNECTED = True
    # Write a non-JSON, non-gzipped value directly.
    fake._db["aurem:scan_textcache:o/r@sha"] = b"\x00\x01\x02not-json-at-all"
    out = await sc.get_cached_text_cache("o", "r", "sha")
    assert out is None        # never raises


@pytest.mark.asyncio
async def test_hit_miss_counters(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379")
    fake = _FakeRedis()
    sc._CLIENT = fake
    sc._CONNECTED = True
    # First call → miss.
    assert await sc.get_cached_text_cache("o", "r", "sha") is None
    # Now store + read again → hit.
    await sc.put_cached_text_cache("o", "r", "sha", {"a": "b"})
    assert await sc.get_cached_text_cache("o", "r", "sha") == {"a": "b"}
    s = sc.get_scan_cache_stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["writes"] == 1
    assert s["hit_rate_pct"] == 50.0


def test_stats_shape_when_redis_off():
    s = sc.get_scan_cache_stats()
    assert s["redis_configured"] is False
    assert s["redis_connected"] is False
    assert s["ttl_seconds"] == 24 * 3600
    assert s["max_entry_bytes"] == 6 * 1024 * 1024
    assert s["hit_rate_pct"] == 0.0


def test_key_format():
    assert sc._key("octo", "demo", "abc123") == \
        "aurem:scan_textcache:octo/demo@abc123"
