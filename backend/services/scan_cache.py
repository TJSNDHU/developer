"""
services/scan_cache.py — Iter 212m-79

Cross-pod scan text-cache dedup.

When a developer runs a Codebase Health scan (or Vanguard scan), we
walk the repo tree + fetch every scannable file via the GitHub API.
That's ~50-600 GitHub calls per scan.  Once we've done this work for
a given `owner/repo@tree_sha`, the result is BIT-FOR-BIT identical
for any other developer scanning the same SHA — the GitHub fetch
budget is being wasted.

This module wraps the text_cache dict in a Redis-backed key/value
store keyed on `aurem:scan_textcache:{owner}/{repo}@{tree_sha}`.

  • Cache hit  → return the dict instantly, skip GitHub entirely.
  • Cache miss → scanner does the normal fetch, we write the result
                 with a 24-hour TTL.

Storage characteristics
-----------------------
  • TTL: 24 hours (commits invalidate via different tree_sha).
  • Per-entry size cap: 6 MB (refuse to cache giants — saves Redis
    RAM; the miss path still works fine).
  • Compression: gzip (text caches compress ~5×).
  • Connection: lazy + retried.  Redis down → silent miss → pod
    fetches GitHub normally.

Public API
----------
  • await get_cached_text_cache(owner, repo, sha)  → dict | None
  • await put_cached_text_cache(owner, repo, sha, text_cache) → bool
  • get_scan_cache_stats() → dict (hits, misses, last_size_bytes…)
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_TTL_S          = 24 * 3600       # 24 hours
_MAX_ENTRY_BYTES = 6 * 1024 * 1024  # 6 MB per cached repo bundle
_PREFIX         = "aurem:scan_textcache:"

# ── Connection state (mirrors admin_analytics_cache shape) ───────────
_CLIENT:    object = None
_TRIED:     bool   = False
_CONNECTED: bool   = False

# ── In-process counters for observability ────────────────────────────
_stats = {
    "hits":              0,
    "misses":            0,
    "writes":            0,
    "skipped_too_big":   0,
    "errors":            0,
    "last_hit_at":       None,
    "last_size_bytes":   0,
}


async def _ensure_redis():
    """Lazy connect.  Returns client or None.  Reconnect attempted on
    each call when the previous attempt failed."""
    global _CLIENT, _TRIED, _CONNECTED
    url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        return None
    if _CLIENT is not None and _CONNECTED:
        return _CLIENT
    try:
        from redis import asyncio as aioredis
        client = aioredis.from_url(
            url, encoding=None, decode_responses=False,
            socket_connect_timeout=2.0, socket_timeout=3.0,
            retry_on_timeout=False, health_check_interval=30,
        )
        await client.ping()
        _CLIENT = client
        _CONNECTED = True
        if not _TRIED:
            logger.info("scan_cache: Redis backend live (%s)",
                        url.split("@")[-1])
            _TRIED = True
        return client
    except Exception as e:
        _CLIENT = None
        _CONNECTED = False
        if not _TRIED:
            logger.info(
                "scan_cache: Redis unavailable (%s); cross-pod dedup OFF",
                type(e).__name__,
            )
            _TRIED = True
        return None


def _key(owner: str, repo: str, sha: str) -> str:
    return f"{_PREFIX}{owner}/{repo}@{sha}"


async def get_cached_text_cache(
    owner: str, repo: str, sha: Optional[str],
) -> Optional[dict[str, str]]:
    """Returns the dict {path: text} if a previously-built bundle for
    `owner/repo@sha` is in Redis, else None.  Never raises."""
    if not sha or not owner or not repo:
        return None
    client = await _ensure_redis()
    if client is None:
        _stats["misses"] += 1
        return None
    try:
        raw = await client.get(_key(owner, repo, sha))
        if raw is None:
            _stats["misses"] += 1
            return None
        try:
            buf = gzip.decompress(raw)
        except Exception:
            buf = raw  # tolerate non-gzipped legacy values
        data = json.loads(buf.decode("utf-8"))
        if not isinstance(data, dict):
            _stats["misses"] += 1
            return None
        _stats["hits"] += 1
        _stats["last_hit_at"] = time.time()
        _stats["last_size_bytes"] = len(raw)
        logger.info(
            "scan_cache HIT %s/%s@%s (%d files, %d bytes wire)",
            owner, repo, sha[:8], len(data), len(raw),
        )
        return data
    except Exception as e:
        _stats["errors"] += 1
        logger.debug("scan_cache get failed for %s/%s@%s: %r",
                     owner, repo, sha[:8] if sha else "?", e)
        return None


async def put_cached_text_cache(
    owner: str, repo: str, sha: Optional[str],
    text_cache: dict[str, str],
) -> bool:
    """Stores the bundle with TTL.  Returns True on success.  Never
    raises — write failure must NEVER block the scan response."""
    if not sha or not owner or not repo or not text_cache:
        return False
    client = await _ensure_redis()
    if client is None:
        return False
    try:
        payload = json.dumps(text_cache, default=str).encode("utf-8")
        compressed = gzip.compress(payload, compresslevel=5)
        if len(compressed) > _MAX_ENTRY_BYTES:
            _stats["skipped_too_big"] += 1
            logger.info(
                "scan_cache SKIP-TOO-BIG %s/%s@%s (%d bytes > %d cap)",
                owner, repo, sha[:8], len(compressed), _MAX_ENTRY_BYTES,
            )
            return False
        await client.set(_key(owner, repo, sha), compressed, ex=_TTL_S)
        _stats["writes"] += 1
        _stats["last_size_bytes"] = len(compressed)
        logger.info(
            "scan_cache STORE %s/%s@%s (%d files, %d bytes wire, ttl=%ds)",
            owner, repo, sha[:8], len(text_cache), len(compressed), _TTL_S,
        )
        return True
    except Exception as e:
        _stats["errors"] += 1
        logger.debug("scan_cache put failed for %s/%s@%s: %r",
                     owner, repo, sha[:8] if sha else "?", e)
        return False


def get_scan_cache_stats() -> dict:
    """Lightweight introspection for /admin/cache/scan-stats."""
    total = _stats["hits"] + _stats["misses"]
    hit_rate = (_stats["hits"] / total * 100.0) if total > 0 else 0.0
    return {
        "redis_configured": bool((os.getenv("REDIS_URL") or "").strip()),
        "redis_connected":  bool(_CONNECTED),
        "ttl_seconds":      _TTL_S,
        "max_entry_bytes":  _MAX_ENTRY_BYTES,
        **_stats,
        "hit_rate_pct":     round(hit_rate, 1),
    }


def _reset_for_tests() -> None:
    global _CLIENT, _TRIED, _CONNECTED
    _CLIENT = None
    _TRIED = False
    _CONNECTED = False
    for k in list(_stats.keys()):
        if isinstance(_stats[k], int):
            _stats[k] = 0
    _stats["last_hit_at"] = None
    _stats["last_size_bytes"] = 0
