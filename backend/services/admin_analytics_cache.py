"""
services/admin_analytics_cache.py — Iter 212m-71
================================================
In-memory TTL cache for the admin analytics aggregations.

Why this exists:
    The admin dashboard fires 5+ heavy aggregations on every page-load
    (`/insights/activation-funnel`, `/admin/users`, `/admin/payments`,
    `/admin/support` etc.).  Each one scans dev_users + cto_projects +
    chat_sessions + cto_payments.  Hitting them 10× while clicking
    around the admin panel = ~50 round-trips and ~30 s of cumulative
    Mongo time.

    Founder UI tolerates 30-60 s of staleness — the data only matters
    in trends.  Wrapping these aggregations in a per-key TTL cache
    drops repeat loads from 6 s → 5 ms.

Public API:
    `await cached_agg(key, ttl, builder)`
        — Returns the cached value if fresh; else awaits `builder()`,
          caches the result, returns it.  Per-key locks ensure that
          concurrent callers don't all stampede the same aggregation
          (only one runs; others await the same future).

    `invalidate(key=None)`
        — Drop a single key (or the whole cache if `key=None`).

This module is intentionally tiny + dependency-free — no Redis, no
external state.  Each uvicorn worker keeps its own cache; the 60 s
TTL is short enough that worker-to-worker drift is invisible.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# {key: (expires_at_unix, value)}
_STORE: dict[str, tuple[float, Any]] = {}
# {key: asyncio.Lock()}  — guards single-flight per key
_LOCKS: dict[str, asyncio.Lock] = {}


def _get_lock(key: str) -> asyncio.Lock:
    lk = _LOCKS.get(key)
    if lk is None:
        lk = _LOCKS[key] = asyncio.Lock()
    return lk


async def cached_agg(
    key: str,
    ttl: float,
    builder: Callable[[], Awaitable[Any]],
) -> Any:
    """Get the value for `key` from cache, or call `builder()` to
    compute + store it.  Single-flight semantics — concurrent callers
    for the same key serialise on the per-key lock so the heavy work
    only runs once.

    Args:
        key:     Cache key (any string).  Use a stable namespace
                 (e.g. ``admin:funnel:7d``).
        ttl:     Time-to-live in seconds.  60 is a good default for
                 admin analytics.
        builder: Async no-arg callable that returns the value to
                 cache.  Called only on cold-miss / expiry.
    """
    now = time.monotonic()
    hit = _STORE.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    lock = _get_lock(key)
    async with lock:
        # Re-check after acquiring the lock — another coroutine may
        # have just populated the cache while we were waiting.
        hit = _STORE.get(key)
        now = time.monotonic()
        if hit is not None and hit[0] > now:
            return hit[1]
        try:
            value = await builder()
        except Exception:
            # On failure, do NOT cache.  A transient Mongo blip should
            # not blackhole the endpoint for the full TTL window.
            raise
        _STORE[key] = (now + max(1.0, float(ttl)), value)
        return value


def invalidate(key: Optional[str] = None) -> int:
    """Drop a specific key (or the entire cache if `key=None`).
    Returns the number of entries removed."""
    if key is None:
        n = len(_STORE)
        _STORE.clear()
        return n
    if key in _STORE:
        del _STORE[key]
        return 1
    return 0


def stats() -> dict:
    """Lightweight introspection for the admin /db-health endpoint."""
    now = time.monotonic()
    return {
        "entries":     len(_STORE),
        "fresh":       sum(1 for exp, _ in _STORE.values() if exp > now),
        "stale":       sum(1 for exp, _ in _STORE.values() if exp <= now),
        "keys_sample": sorted(_STORE.keys())[:20],
    }
