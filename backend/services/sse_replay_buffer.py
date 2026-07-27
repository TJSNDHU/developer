"""
services/sse_replay_buffer.py — Iter 309 · Batch-2 Item 6

Per-loop-session ring buffer for SSE reconnect replay.

Problem
-------
`GET /loop/{loop_id}/stream` currently caps at STREAM_MAX_S (20 min
in prod) but a real loop can wall-clock ~40 min.  When the browser's
EventSource auto-reconnects — either at the cap or on network
blip — the client re-subscribes to the live event queue only, so
every event emitted DURING the disconnect gap is silently lost.
Founder-reported UI symptom: "screen stuck on stale phase for 20 min,
then jumps to COMPLETED".

Fix
---
1. Assign every emitted event a monotonic sequence number
   `{loop_id}:{seq}` and store the last N in a per-session ring
   buffer.  Kept in-process — good enough for single-worker preview
   AND per-worker prod (each worker replays what IT emitted; the
   client's Last-Event-ID will match the worker that sent it).
2. On reconnect, `GET /loop/{loop_id}/stream` reads the
   `Last-Event-ID` header (browser-native), parses the seq portion,
   and replays every buffered event with seq > last_seen BEFORE
   attaching to the live queue.  No missed events; no duplicate
   events (client also dedups by id ≤ last-seen as belt-and-braces).
3. TTL cleanup — a background task run by `main.py`'s periodic
   housekeeping evicts buffers whose loop has ended > TTL_S ago.

Design constraints (founder-locked)
-----------------------------------
• In-memory only.  No new Mongo collection, no Redis dependency.
• Cap `MAX_EVENTS_PER_LOOP = 200` — a 40-min loop emits ~120
  meaningful events (heartbeat + phase transitions + per-file
  parliament).  200 gives generous headroom without unbounded RAM.
• No WebSockets, no polling fallback (out-of-scope per Item 6 spec).
"""
from __future__ import annotations
import asyncio
import time
from collections import deque
from typing import Optional


# ── Tunables (env-overridable, sensible defaults for prod/preview) ──
MAX_EVENTS_PER_LOOP: int = 200
BUFFER_TTL_S:        int = 45 * 60   # 45 min — past the STREAM_MAX_S=1200 s cap AND a full 40 min loop
BROWSER_RECONNECT_MS: int = 3000     # what we tell the browser via `retry:`


# ── Per-loop state ──────────────────────────────────────────────────
class _LoopBuf:
    """Owns the deque + seq counter + last-touch time for one loop."""
    __slots__ = ("events", "next_seq", "last_touched_at", "ended_at")

    def __init__(self) -> None:
        self.events: deque[tuple[int, dict]] = deque(maxlen=MAX_EVENTS_PER_LOOP)
        self.next_seq: int = 0
        self.last_touched_at: float = time.time()
        self.ended_at: Optional[float] = None    # set on TERMINAL events


# Module-level registry. Not asyncio.Lock — Python's GIL makes the
# dict-mutation operations we do (append to deque + int increment)
# atomic enough for our single-worker-per-loop model; a real
# multi-worker deploy would need Redis anyway.
_BUFFERS: dict[str, _LoopBuf] = {}


# ── Public API ──────────────────────────────────────────────────────
def record(loop_id: str, event: dict) -> tuple[int, str]:
    """Assign a seq + persist in the ring buffer.  Returns (seq, id).

    `id` is the SSE `id:` line value — `{loop_id}:{seq}`.  Browsers
    remember it in `Last-Event-ID` across reconnects automatically.
    """
    buf = _BUFFERS.get(loop_id)
    if buf is None:
        buf = _LoopBuf()
        _BUFFERS[loop_id] = buf
    seq = buf.next_seq
    buf.next_seq += 1
    buf.events.append((seq, event))
    buf.last_touched_at = time.time()
    # Mark ended for TTL cleanup — but keep the buffer alive so a
    # late reconnect (e.g. user backgrounded the tab for 30 min)
    # can still replay the final events.
    st = str(event.get("state") or "").lower()
    if st in ("completed", "failed", "aborted"):
        buf.ended_at = time.time()
    return seq, f"{loop_id}:{seq}"


def parse_last_event_id(header_value: str, loop_id: str) -> int:
    """Return the seq the client last saw, or -1 if header is
    missing / malformed / from a different loop.  Never raises."""
    if not header_value:
        return -1
    try:
        # Expected `{loop_id}:{seq}` — but also accept a bare integer
        # in case a proxy strips the colon.
        if ":" in header_value:
            lid, seq_s = header_value.rsplit(":", 1)
            if lid and lid != loop_id:
                return -1
            return int(seq_s)
        return int(header_value)
    except (ValueError, AttributeError):
        return -1


def replay_after(loop_id: str, after_seq: int) -> list[tuple[int, dict]]:
    """Return the buffered events with seq > after_seq, oldest first.

    Empty list if:
      • No buffer for this loop (worker restart, buffer expired).
      • after_seq is already at/past the newest event (client is
        already up-to-date — nothing to replay).
    """
    buf = _BUFFERS.get(loop_id)
    if buf is None:
        return []
    if after_seq < 0:
        return list(buf.events)
    return [(s, ev) for (s, ev) in buf.events if s > after_seq]


def evict_expired() -> int:
    """Called from `main.py`'s periodic housekeeping.  Drops
    buffers whose loop ended > BUFFER_TTL_S ago.  Returns the count
    evicted, for logging."""
    now = time.time()
    dead = [lid for lid, b in _BUFFERS.items()
            if b.ended_at is not None and (now - b.ended_at) > BUFFER_TTL_S]
    for lid in dead:
        _BUFFERS.pop(lid, None)
    return len(dead)


def buffer_stats() -> dict:
    """Read-only diagnostic — used by /admin/loop-metrics later
    (Item 9 wiring) so the founder can see per-loop last-seq + how
    many bytes the buffer holds."""
    stats: dict = {}
    for lid, b in _BUFFERS.items():
        stats[lid] = {
            "next_seq":       b.next_seq,
            "buffered":       len(b.events),
            "last_touched":   b.last_touched_at,
            "ended_at":       b.ended_at,
        }
    return stats


def buffer_events(loop_id: str, max_events: int = 200) -> list:
    """Iter 316 · Fix D — raw replay-buffer events for a single loop.

    Powers `/admin/loop-inspect/{loop_id}` — used to diagnose the
    SSE-delivery gap that produced founder's simple-task stall on
    2026-07-27 (chip said AWAITING APPROVAL, chat stuck on
    "Generating plan…"). Read-only, redacts nothing (this is admin-
    scoped inspection; the replay contents are exactly what the
    frontend would have received).

    Returns newest-first, capped at `max_events` (default 200).
    """
    buf = _BUFFERS.get(loop_id)
    if buf is None:
        return []
    # events is a list of (seq, event) tuples, oldest first. Take last N.
    tail = list(buf.events)[-max(1, min(max_events, 1000)):]
    # Newest first for readability, with seq inlined for grep-audit.
    return [{"seq": s, "event": ev} for (s, ev) in reversed(tail)]


def _reset_for_tests() -> None:
    """Test-only helper.  Not exported at module top-level to signal
    intent, but importable when a test needs a clean slate."""
    _BUFFERS.clear()
