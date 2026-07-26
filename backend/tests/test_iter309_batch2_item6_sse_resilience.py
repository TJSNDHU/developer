"""
test_iter309_batch2_item6_sse_resilience.py — Batch 2 · Item 6

Regression tests for SSE reconnect resilience via per-session ring
buffer + `Last-Event-ID` header replay + `retry:` preamble.

Test A — kill SSE mid-execute in harness, reconnect with
         Last-Event-ID, assert zero missed + zero duplicate events
         applied.
Test B — post-reconnect UI state object identical to an
         uninterrupted run's final state.
"""
from __future__ import annotations

import pytest

from services import sse_replay_buffer as buf


@pytest.fixture(autouse=True)
def _clean_buffers():
    buf._reset_for_tests()
    yield
    buf._reset_for_tests()


# ── record / replay basic contract ─────────────────────────────────
def test_record_assigns_monotonic_seq():
    s1, id1 = buf.record("L1", {"state": "planning", "ts": 1})
    s2, id2 = buf.record("L1", {"state": "planning", "ts": 2})
    s3, id3 = buf.record("L1", {"state": "executing", "ts": 3})
    assert (s1, s2, s3) == (0, 1, 2)
    assert (id1, id2, id3) == ("L1:0", "L1:1", "L1:2")


def test_record_isolates_loops():
    buf.record("A", {"state": "planning"})
    s, i = buf.record("B", {"state": "planning"})
    assert (s, i) == (0, "B:0")


def test_parse_last_event_id_variants():
    assert buf.parse_last_event_id("L1:5", "L1") == 5
    assert buf.parse_last_event_id("42",   "L1") == 42
    assert buf.parse_last_event_id("",     "L1") == -1
    assert buf.parse_last_event_id("garbage", "L1") == -1
    # Wrong loop_id — ignore (client reconnected to a different loop)
    assert buf.parse_last_event_id("Other:9", "L1") == -1


def test_replay_after_returns_only_newer():
    for i in range(5):
        buf.record("L", {"state": "executing", "i": i})
    got = buf.replay_after("L", 2)
    assert [ev.get("i") for _s, ev in got] == [3, 4]
    assert [s for s, _ev in got] == [3, 4]


def test_replay_missing_loop_is_empty():
    assert buf.replay_after("NEVER", 0) == []


def test_replay_after_neg_one_returns_all():
    for i in range(3):
        buf.record("L", {"i": i})
    got = buf.replay_after("L", -1)
    assert [ev["i"] for _s, ev in got] == [0, 1, 2]


# ── Test A: kill mid-execute → reconnect → zero missed, zero dupes ─
def test_A_kill_midstream_reconnect_no_gap_no_dupes():
    """Simulate: client subscribes, receives events 0-4, connection
    drops (server keeps emitting 5-9), client reconnects with
    Last-Event-ID=L:4, replays 5-9. Client's applied set must equal
    an uninterrupted subscriber's exactly, no repeats."""
    # Uninterrupted subscriber (baseline).
    baseline_applied: list = []
    for i in range(10):
        _s, _id = buf.record("L", {"seq_hint": i, "state": "executing"})
        baseline_applied.append((_s, _id))
    baseline_seqs = [s for s, _ in baseline_applied]
    assert baseline_seqs == list(range(10))

    # Interrupted subscriber: saw 0-4, then dropped, reconnected
    # with Last-Event-ID = "L:4".  Server MUST replay 5-9.
    resumed = buf.replay_after("L", buf.parse_last_event_id("L:4", "L"))
    resumed_seqs = [s for s, _ev in resumed]
    # Zero missed: covers exactly the gap 5-9.
    assert resumed_seqs == [5, 6, 7, 8, 9]
    # Zero dupes: no seq ≤ 4 in the resume payload.
    assert all(s > 4 for s in resumed_seqs)


# ── Test B: post-reconnect final state === uninterrupted final ─────
def test_B_post_reconnect_state_matches_uninterrupted():
    """Both subscribers should end up with the same ordered event
    list and the same final `state` string."""
    # Simulate a full loop: plan → execute → verify → scan → ship → completed.
    events = [
        {"state": "planning",   "phase": "plan"},
        {"state": "executing",  "phase": "execute"},
        {"state": "verifying",  "phase": "verify"},
        {"state": "scanning",   "phase": "scan"},
        {"state": "shipping",   "phase": "ship"},
        {"state": "completed",  "phase": "ship"},
    ]
    for ev in events:
        buf.record("R", ev)

    # Uninterrupted client accumulates from the live stream.
    live_view = [ev for _s, ev in buf.replay_after("R", -1)]

    # Interrupted client: got events 0-2 live, then reconnect with
    # Last-Event-ID=R:2, replays 3-5 (verify + scan + ship + completed).
    early = [ev for _s, ev in buf.replay_after("R", -1) if _s <= 2]
    late  = [ev for _s, ev in buf.replay_after("R", 2)]
    reconnect_view = early + late

    assert live_view == reconnect_view
    # Terminal state matches.
    assert live_view[-1]["state"] == "completed"
    assert reconnect_view[-1]["state"] == "completed"


# ── TTL eviction ────────────────────────────────────────────────────
def test_evict_expired_drops_ended_and_stale():
    import time
    buf.record("done", {"state": "completed"})
    buf.record("live", {"state": "executing"})
    # Force the 'done' buffer's ended_at into the past.
    b = buf._BUFFERS["done"]
    b.ended_at = time.time() - (buf.BUFFER_TTL_S + 60)
    n = buf.evict_expired()
    assert n == 1
    assert "done" not in buf._BUFFERS
    assert "live" in buf._BUFFERS       # still alive — not ended


def test_ring_buffer_capacity_bounds_memory():
    # Push 300 events; only the last MAX_EVENTS_PER_LOOP survive.
    for i in range(300):
        buf.record("C", {"i": i})
    b = buf._BUFFERS["C"]
    assert len(b.events) == buf.MAX_EVENTS_PER_LOOP
    # Next seq counter is still correct.
    assert b.next_seq == 300
    # Oldest surviving event's seq is 300 - MAX.
    first_seq, _ = list(b.events)[0]
    assert first_seq == 300 - buf.MAX_EVENTS_PER_LOOP
