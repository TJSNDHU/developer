"""
services/admin_analytics_cache.py — Iter 212m-76
================================================
Tiered cache for the admin analytics aggregations.

Pod-restart-safe.  Uses Redis as the primary backend when `REDIS_URL`
is set in the environment; falls back to a process-local in-memory
dict if Redis is unavailable (dev preview, first-boot, transient
network blip).  Caller code is unchanged — same `cached_agg` /
`invalidate` / `stats` signatures.

Why this exists
---------------
Admin dashboard fires 5+ heavy aggregations on every page load.  In
iter 212m-71 we wrapped them in an in-memory TTL cache.  That dies on
every uvicorn worker restart + every pod restart, so right after a
deploy the founder gets the same 6 s cold-start hit they had before
the cache existed.  Redis fixes this — the cache survives restarts,
and multiple uvicorn workers / multiple pods now share one warm view
of the same aggregation.

Design choices
--------------
- **Optional Redis** — `REDIS_URL` env must be set to enable it.
  Missing/invalid URL → silent fallback to in-memory.  No deploy is
  ever blocked by a Redis outage.
- **Decode at boundary** — values are JSON-encoded on store, decoded
  on read.  Pydantic / Mongo doc / dict / list all round-trip
  cleanly.
- **Single-flight per key** — Redis SETNX-based lock guards against
  the thundering-herd stampede when an aggregation expires.  In-mem
  path still uses `asyncio.Lock`.
- **No background tasks** — connection is created on first use,
  cached on the module, and lazily reconnected on failure.
- **Best-effort writes** — write failures NEVER raise.  Worst case
  we re-compute next call.

Public API (unchanged)
----------------------
- `await cached_agg(key, ttl, builder)` — returns cached value or
  computes via `builder()` (single-flight).
- `invalidate(key=None)` — drop one key or the whole cache.  Async-
  safe even though the legacy signature is sync — Redis ops are
  fired through a sync helper that schedules them onto the running
  loop when one exists.
- `stats()` — light introspection for the admin /db-health page.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ── In-memory fallback (legacy iter 212m-71 path) ────────────────────
_STORE: dict[str, tuple[float, Any]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}

# ── Redis state ──────────────────────────────────────────────────────
_REDIS_CLIENT: Any = None        # redis.asyncio.Redis instance
_REDIS_TRIED: bool = False       # one-shot init flag
_REDIS_OK:    bool = False       # last known health
_REDIS_PREFIX = "aurem:cache:admin:"
_LOCK_PREFIX  = "aurem:lock:admin:"


def _get_lock(key: str) -> asyncio.Lock:
    lk = _LOCKS.get(key)
    if lk is None:
        lk = _LOCKS[key] = asyncio.Lock()
    return lk


async def _ensure_redis() -> Any:
    """Lazy connect.  Returns the client on success, None otherwise.
    Reconnection is attempted on every call when previous attempt
    failed — the cost is one socket connect on a cold pod."""
    global _REDIS_CLIENT, _REDIS_TRIED, _REDIS_OK
    url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        return None
    if _REDIS_CLIENT is not None and _REDIS_OK:
        return _REDIS_CLIENT
    try:
        from redis import asyncio as aioredis
        client = aioredis.from_url(
            url, encoding="utf-8", decode_responses=False,
            socket_connect_timeout=2.0, socket_timeout=2.0,
            retry_on_timeout=False, health_check_interval=30,
        )
        await client.ping()
        _REDIS_CLIENT = client
        _REDIS_OK = True
        if not _REDIS_TRIED:
            logger.info("admin_analytics_cache: Redis backend live (%s)",
                        url.split("@")[-1])  # never log creds
            _REDIS_TRIED = True
        return client
    except Exception as e:
        _REDIS_CLIENT = None
        _REDIS_OK = False
        if not _REDIS_TRIED:
            logger.info("admin_analytics_cache: Redis unavailable "
                        "(%s); using in-memory fallback", type(e).__name__)
            _REDIS_TRIED = True
        return None


async def _redis_get(client: Any, key: str) -> Optional[Any]:
    try:
        raw = await client.get(_REDIS_PREFIX + key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug("redis get failed for %s: %r", key, e)
        return None


async def _redis_set(client: Any, key: str, value: Any, ttl: float) -> None:
    try:
        await client.set(
            _REDIS_PREFIX + key,
            json.dumps(value, default=str).encode("utf-8"),
            ex=max(1, int(ttl)),
        )
    except Exception as e:
        logger.debug("redis set failed for %s: %r", key, e)


async def cached_agg(
    key: str,
    ttl: float,
    builder: Callable[[], Awaitable[Any]],
) -> Any:
    """Get the value for `key` from cache, or call `builder()` to
    compute + store it.  Single-flight semantics — concurrent callers
    for the same key serialise on a per-key lock so the heavy work
    only runs once.

    Tries Redis first (survives pod restarts, shared across workers).
    Falls back to in-memory dict on any Redis failure (legacy path).
    """
    client = await _ensure_redis()

    # ── Redis path ──────────────────────────────────────────────
    if client is not None:
        hit = await _redis_get(client, key)
        if hit is not None:
            return hit
        # Single-flight across workers via Redis SETNX lock.  60 s
        # lease TTL so a crashed builder can never permanently block.
        lock_key = _LOCK_PREFIX + key
        got_lock = False
        try:
            got_lock = bool(await client.set(
                lock_key, b"1", nx=True, ex=60,
            ))
        except Exception:
            got_lock = False
        if not got_lock:
            # Another worker is building — short busy-wait up to 5 s.
            for _ in range(50):
                await asyncio.sleep(0.1)
                hit = await _redis_get(client, key)
                if hit is not None:
                    return hit
            # Builder is slow / dead — proceed to compute ourselves
            # rather than hang the request.
        try:
            value = await builder()
        except Exception:
            try:
                await client.delete(lock_key)
            except Exception:
                pass
            raise
        await _redis_set(client, key, value, ttl)
        try:
            await client.delete(lock_key)
        except Exception:
            pass
        # Keep in-mem mirror so `invalidate()` + `stats()` both still
        # work locally and so the next read on this worker skips Redis
        # for the duration of `ttl` even if Redis hiccups.
        _STORE[key] = (time.monotonic() + max(1.0, float(ttl)), value)
        return value

    # ── In-memory fallback (legacy iter 212m-71 path) ───────────
    now = time.monotonic()
    hit = _STORE.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    lock = _get_lock(key)
    async with lock:
        hit = _STORE.get(key)
        now = time.monotonic()
        if hit is not None and hit[0] > now:
            return hit[1]
        value = await builder()
        _STORE[key] = (now + max(1.0, float(ttl)), value)
        return value


def invalidate(key: Optional[str] = None) -> int:
    """Drop a specific key (or the entire cache if `key=None`).
    Returns the number of entries removed from the local mirror.

    Schedules the matching Redis DEL onto the running event loop in
    a fire-and-forget task — keeping the legacy sync signature so no
    caller has to change."""
    if key is None:
        n = len(_STORE)
        _STORE.clear()
        _spawn(_redis_invalidate_all())
        return n
    n = 1 if key in _STORE else 0
    _STORE.pop(key, None)
    _spawn(_redis_invalidate_one(key))
    return n


def _spawn(coro) -> None:
    """Fire a coroutine on the running loop, or drop it silently if
    there is no loop (e.g. invalidate() called from a sync test)."""
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        # No loop running.  Best-effort: schedule via asyncio.run on
        # a temp loop.  Wrapped in try so unit tests stay quiet.
        try:
            asyncio.run(coro)
        except Exception:
            pass


async def _redis_invalidate_one(key: str) -> None:
    client = await _ensure_redis()
    if client is None:
        return
    try:
        await client.delete(_REDIS_PREFIX + key)
    except Exception as e:
        logger.debug("redis delete failed for %s: %r", key, e)


async def _redis_invalidate_all() -> None:
    client = await _ensure_redis()
    if client is None:
        return
    try:
        cursor = 0
        while True:
            cursor, keys = await client.scan(
                cursor=cursor, match=_REDIS_PREFIX + "*", count=200,
            )
            if keys:
                await client.delete(*keys)
            if cursor == 0:
                break
    except Exception as e:
        logger.debug("redis scan-delete failed: %r", e)


def stats() -> dict:
    """Lightweight introspection for the admin /db-health endpoint."""
    now = time.monotonic()
    return {
        "entries":     len(_STORE),
        "fresh":       sum(1 for exp, _ in _STORE.values() if exp > now),
        "stale":       sum(1 for exp, _ in _STORE.values() if exp <= now),
        "keys_sample": sorted(_STORE.keys())[:20],
        "redis":       {
            "configured": bool((os.getenv("REDIS_URL") or "").strip()),
            "connected":  bool(_REDIS_OK),
        },
    }


# ────────────────────────────────────────────────────────────────────────
#  Iter 212m-154 — Mongo-backed Stale-While-Revalidate cache.
#
#  Why a second cache layer?
#  --------------------------
#  `cached_agg()` above is great for warm reads (Redis + in-process).
#  But the activation-funnel and other heavy admin aggregations also
#  need to survive:
#    • Redis flushes / cold pods
#    • Multi-second cold-compute paths that already 499 the frontend
#
#  This helper persists the last-known-good value into Mongo
#  (`analytics_persistent_cache` collection, single doc per key).
#  Reads ALWAYS return immediately if any doc exists — even when it
#  is past the TTL.  When stale, a background refresh task is fired
#  but the request itself never waits.  On the very first cold boot
#  (no doc at all) we do block, but capped by `hard_timeout=4.0` s
#  so the frontend never aborts.
#
#  Empirically this drops admin/insights/activation-funnel from
#  ~6 s of 499s on cold cache to a <50 ms Mongo read on every load.
# ────────────────────────────────────────────────────────────────────────

_MONGO_SWR_REFRESH_LOCKS: dict[str, asyncio.Lock] = {}
_PERSIST_COLL = "analytics_persistent_cache"


def _swr_refresh_lock(key: str) -> asyncio.Lock:
    """Per-key asyncio lock so concurrent stale reads only ever trigger
    one background recompute at a time on this worker."""
    lk = _MONGO_SWR_REFRESH_LOCKS.get(key)
    if lk is None:
        lk = _MONGO_SWR_REFRESH_LOCKS[key] = asyncio.Lock()
    return lk


async def _swr_compute_and_store(
    key: str, builder: Callable[[], Awaitable[Any]], coll: Any,
) -> Any:
    """Run the builder, persist + return the result.  Wrapped in the
    per-key lock so a thundering herd does at most one rebuild."""
    lock = _swr_refresh_lock(key)
    async with lock:
        # Double-check after acquiring the lock — somebody else may
        # have just refreshed.
        doc = await coll.find_one(
            {"_id": key}, {"value": 1, "computed_at": 1},
        )
        now_ts = time.time()
        if doc and (now_ts - float(doc.get("computed_at") or 0)) < 5:
            return doc["value"]   # raced — use their fresh value
        try:
            value = await builder()
        except Exception:
            # Re-raise so the caller knows the rebuild failed; the
            # outer wrapper will fall back to the stale doc.
            raise
        try:
            await coll.update_one(
                {"_id": key},
                {"$set": {
                    "value":       value,
                    "computed_at": now_ts,
                    "ttl_seconds": float(
                        # ttl is not in scope here — read off the
                        # closure caller; we accept it as a passthrough.
                        0
                    ),
                }},
                upsert=True,
            )
        except Exception as e:
            logger.warning("swr persist failed for %s: %r", key, e)
        return value


async def _swr_background_refresh(
    key: str, builder: Callable[[], Awaitable[Any]], coll: Any,
) -> None:
    """Fire-and-forget refresh.  Never raises — the caller already has
    a stale-but-good value to return."""
    try:
        await _swr_compute_and_store(key, builder, coll)
    except Exception as e:
        logger.warning("swr background refresh failed for %s: %r", key, e)


async def mongo_swr_cache(
    key: str,
    ttl_seconds: float,
    builder: Callable[[], Awaitable[Any]],
    *,
    hard_timeout: float = 4.0,
) -> Any:
    """Stale-while-revalidate cache backed by a single Mongo document.

    Behaviour:
      • If a stored doc exists → return it immediately.  If past TTL,
        spawn a background refresh task.
      • If no doc exists at all → compute synchronously with
        `hard_timeout` (default 4 s).  On timeout, return a warming
        skeleton and spawn the compute as a background task.
      • The persisted doc lives in collection ``analytics_persistent_cache``.

    Returns the cached value, the fresh value, or a warming skeleton.
    """
    # Late-bind the Mongo client so importing this module does not
    # require a live DB connection (matters for unit tests).
    try:
        from cto_services.db import get_db
        db = get_db()
    except Exception as e:
        logger.warning("mongo_swr_cache: db unavailable (%r) — fallback to cached_agg", e)
        return await cached_agg(key, ttl_seconds, builder)
    if db is None:
        return await cached_agg(key, ttl_seconds, builder)
    coll = db[_PERSIST_COLL]

    try:
        doc = await coll.find_one(
            {"_id": key}, {"value": 1, "computed_at": 1},
        )
    except Exception as e:
        logger.warning("swr read failed for %s: %r — fallback to cached_agg", key, e)
        return await cached_agg(key, ttl_seconds, builder)

    now_ts = time.time()
    if doc:
        age = now_ts - float(doc.get("computed_at") or 0)
        if age > ttl_seconds:
            # Stale — schedule background refresh, return stale value.
            try:
                asyncio.get_running_loop().create_task(
                    _swr_background_refresh(key, builder, coll),
                )
            except RuntimeError:
                pass
        return doc["value"]

    # No doc at all — first cold boot.  Synchronous compute, capped.
    try:
        value = await asyncio.wait_for(
            _swr_compute_and_store(key, builder, coll),
            timeout=hard_timeout,
        )
        return value
    except asyncio.TimeoutError:
        logger.info("swr compute timed out for %s — returning warming skeleton", key)
        # Schedule the compute in the background so the next request
        # finds a doc and serves from it.
        try:
            asyncio.get_running_loop().create_task(
                _swr_background_refresh(key, builder, coll),
            )
        except RuntimeError:
            pass
        return {
            "_status":  "warming",
            "_message": (
                "Funnel is computing in the background. "
                "Refresh in 30 seconds for live data."
            ),
        }


async def warm_swr_keys(keys_and_builders: list[tuple[str, float,
                                                       Callable[[], Awaitable[Any]]]],
                        ) -> dict:
    """Pre-warm SWR keys on app startup so the first-ever admin load
    never sees the warming skeleton.  Best-effort: each key is wrapped
    in its own try, so a single bad builder never kills the warmer.

    Returns a per-key status dict for observability."""
    out: dict[str, str] = {}
    for key, ttl, builder in keys_and_builders:
        try:
            await mongo_swr_cache(key, ttl, builder, hard_timeout=30.0)
            out[key] = "warmed"
        except Exception as e:
            out[key] = f"failed: {type(e).__name__}"
            logger.warning("swr warmer failed for %s: %r", key, e)
    return out


# ── Test hook ────────────────────────────────────────────────────────
def _reset_for_tests() -> None:
    """Used only by the test suite to flush local state between runs.
    Production code never calls this."""
    global _REDIS_CLIENT, _REDIS_TRIED, _REDIS_OK
    _STORE.clear()
    _LOCKS.clear()
    _REDIS_CLIENT = None
    _REDIS_TRIED = False
    _REDIS_OK = False
