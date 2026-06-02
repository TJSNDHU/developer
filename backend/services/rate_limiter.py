"""
services/rate_limiter.py — async rate limiter (in-memory, per-IP).

Why hand-rolled? `slowapi`'s decorator collides with FastAPI's automatic
dependency injection when the function signature mixes Request, Pydantic
bodies, and BackgroundTasks (the decorator re-orders the params and
FastAPI then misinterprets them as query strings).

This module gives us:
  - `check_rate_limit(key, limit_per_minute)` — returns True if the
    request is allowed, False if it should be 429'd. O(1) per call,
    cheap memory.

Suspended when env RATE_LIMIT_DISABLED=1 (tests).
"""
from __future__ import annotations
import os
import time
from collections import deque, defaultdict
from typing import Deque

_ENABLED = os.getenv("RATE_LIMIT_DISABLED", "0") != "1"

# key → sliding-window timestamps (most recent first). 60-second buckets.
_buckets: dict[str, Deque[float]] = defaultdict(deque)
_WINDOW_SEC = 60.0

# BUG 7 fix — cap the in-memory bucket map so attackers can't OOM the
# process by rotating Bearer-token Authorization headers (or IP-spoofed
# X-Forwarded-For values) on every request. 10K active keys per minute
# is comfortably above any honest traffic pattern.
_MAX_BUCKETS = int(os.getenv("RATE_LIMIT_MAX_BUCKETS", "10000"))


def check_rate_limit(key: str, limit_per_minute: int) -> bool:
    """Returns True if this request is allowed under the per-minute limit."""
    if not _ENABLED:
        return True
    now = time.time()
    # Eject the oldest entry when we're about to add a new one over the cap.
    # Using `next(iter(...))` on a dict gives us the insertion-oldest key
    # in CPython, which is the closest we get to an LRU without an extra
    # data structure. The window check below cleans expired entries anyway.
    if key not in _buckets and len(_buckets) >= _MAX_BUCKETS:
        try:
            oldest = next(iter(_buckets))
            del _buckets[oldest]
        except StopIteration:
            pass
    bucket = _buckets[key]
    # Drop timestamps older than the window
    while bucket and (now - bucket[0]) > _WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= limit_per_minute:
        return False
    bucket.append(now)
    return True


def client_ip_from_request(request) -> str:
    """Extract the real client IP, honoring X-Forwarded-For when set by
    the K8s ingress."""
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if fwd:
        return fwd
    return getattr(request.client, "host", "unknown") or "unknown"
