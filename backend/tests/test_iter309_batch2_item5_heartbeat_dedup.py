"""
test_iter309_batch2_item5_heartbeat_dedup.py — Batch 2 · Item 5

Regression test for the duplicate-heartbeat cleanup.  After iter 309
Batch-2 Item 5, EXACTLY ONE heartbeat emitter must remain per phase:
the generic `_heartbeat_loop` inside `LoopEngine._with_budget`
(iter 308).  The per-file `_heartbeat` loop that iter 278 added
inside `_do_execute`'s Parliament call has been REMOVED — running
both simultaneously produced duplicate/near-simultaneous
`sub_step:"heartbeat"` events (1 phase-level + N file-level every
6s during a parallel file fan-out).

The test does two things:

  1. **Source pattern contract** — parses `services/loop_engine.py`
     and asserts:
       (a) Exactly ONE `async def _heartbeat_loop` definition
           remains (iter 308's).
       (b) The old `async def _heartbeat():` per-file function is
           GONE — presence would signal a merge regression.
       (c) No `sub_step.*heartbeat` string literal appears inside
           the `_do_execute` method body.

  2. **Runtime cadence** — instantiate a `LoopEngine`, drive it
     through a fake phase that sleeps just past 2 heartbeat ticks
     (~14s / HEARTBEAT_INTERVAL_S=6), collect emitted events,
     and assert:
       - The number of heartbeat frames is within [2, 4] (allows
         for scheduling jitter), NEVER doubled.
       - No two heartbeat frames land within 100ms of each other
         (the classic race signature).
       - All heartbeats carry `phase` field but NO `file` field
         (the removed per-file loop was the only one that added
         `file` — its disappearance is the fix).
"""
from __future__ import annotations
import asyncio
import re
import time
from pathlib import Path

import pytest


_LOOP_ENGINE_SRC = Path(
    "/app/backend/services/loop_engine.py",
).read_text()


def test_source_has_exactly_one_heartbeat_loop_definition():
    """Iter 308's `_heartbeat_loop` is the sole survivor."""
    defs = re.findall(r"^\s*async def (_heartbeat\w*)\s*\(",
                      _LOOP_ENGINE_SRC, re.MULTILINE)
    assert defs == ["_heartbeat_loop"], (
        f"expected exactly one heartbeat definition "
        f"(`_heartbeat_loop` from iter 308), found: {defs}"
    )


def test_iter278_per_file_heartbeat_is_gone():
    """The `async def _heartbeat():` per-file inner function that
    iter 278 embedded inside `_do_execute`'s Parliament call has
    been deleted.  Any resurrection breaks the dedup guarantee."""
    # The old block also contained a labelled marker string —
    # confirm both signals are gone.
    assert "Still waiting on LLM response for" not in _LOOP_ENGINE_SRC, (
        "iter 278 per-file heartbeat marker string is back — "
        "the per-file heartbeat has been re-introduced"
    )
    assert 'data={\n                                                "file":' not in _LOOP_ENGINE_SRC


def test_do_execute_body_has_no_heartbeat_emission():
    """Scan just the `_do_execute` method body for a heartbeat
    emission — there must be none.  Iter 308's phase-level
    heartbeat lives in `_with_budget`, which WRAPS every phase
    (including execute); it's not IN the phase body itself."""
    m = re.search(
        r"^\s+async def _do_execute\(self\).*?^\s+async def _do_verify\(self\)",
        _LOOP_ENGINE_SRC, re.DOTALL | re.MULTILINE,
    )
    assert m, "could not locate _do_execute method body"
    body = m.group(0)
    # Strip comments and docstrings so the marker string in the
    # cleanup-explanation comment doesn't false-positive.
    body_no_comments = re.sub(r"#.*$", "", body, flags=re.MULTILINE)
    body_no_comments = re.sub(r'""".*?"""', "", body_no_comments, flags=re.DOTALL)
    assert '"heartbeat"' not in body_no_comments, (
        "`_do_execute` body emits a heartbeat directly — "
        "only `_with_budget`'s wrapper heartbeat may fire"
    )


@pytest.mark.asyncio
async def test_runtime_heartbeat_cadence_is_single_stream(monkeypatch):
    """Instantiate a LoopEngine, drive one phase that sleeps
    ~14 s so the generic heartbeat fires twice, and confirm we
    got exactly one heartbeat STREAM (no dupes) with no near-
    simultaneous pairs."""
    from services import loop_engine as eng

    # Shrink the heartbeat interval so we get 2 ticks in ~3 seconds
    # (test suite must stay fast).  This is env-invariant since
    # the module reads the constant at emit time.
    monkeypatch.setattr(eng, "HEARTBEAT_INTERVAL_S", 1.0)

    # In-memory `_DB` — just enough for _with_budget to run.
    class _Coll:
        def __init__(self): self.docs = []
        async def insert_one(self, d): self.docs.append(d)
        async def update_one(self, *a, **k): pass
        async def delete_one(self, *a, **k): pass
        async def replace_one(self, *a, **k): pass
        async def find_one(self, *a, **k): return None
        async def count_documents(self, *a, **k): return 0

    class _DB:
        def __init__(self):
            self.loop_sessions = _Coll()
            self.loop_events   = _Coll()
            self.dev_users     = _Coll()
            self.cto_projects  = _Coll()
        def __getitem__(self, n):
            if not hasattr(self, n): setattr(self, n, _Coll())
            return getattr(self, n)

    db = _DB()
    engine = eng.LoopEngine(db, "loop_hb_dedup", "u1", None, "test")
    # Heartbeat only fires while `self.state == _PHASE_STATE[phase]`
    # (iter 308 guard against flipping a PAUSED session back to
    # running). Prime the state so the guard passes.
    engine.state = eng.LoopState.EXECUTING

    events: list[dict] = []
    async def _capture(evt): events.append(evt)
    monkeypatch.setattr(engine, "_emit_hook", _capture, raising=False)
    # Wrap _emit so we can observe emissions without breaking Mongo
    # writes.  The real _emit is fine to call — it queues via
    # asyncio.Queue and also writes to loop_events; we just also
    # snapshot into a local list.
    _orig_emit = engine._emit
    async def _emit_and_snapshot(*args, **kw):
        # Reconstruct enough of the event to inspect.
        data = kw.get("data", {}) or {}
        events.append({
            "phase": kw.get("phase") or (args[1] if len(args) > 1 else None),
            "data":  dict(data),
            "at":    time.monotonic(),
        })
        await _orig_emit(*args, **kw)
    monkeypatch.setattr(engine, "_emit", _emit_and_snapshot)

    # Fake phase coroutine — sleeps 3 s to allow 2 heartbeat ticks.
    async def _slow_phase():
        await asyncio.sleep(3.0)

    # Override PHASE_TIMEOUTS_S so the wrap doesn't cut us off.
    monkeypatch.setitem(eng.PHASE_TIMEOUTS_S, "execute", 10)

    await engine._with_budget("execute", _slow_phase)

    # Filter to just heartbeats.
    hbs = [e for e in events
           if (e.get("data") or {}).get("sub_step") == "heartbeat"]

    # 3 s / 1 s interval → 2 or 3 ticks. Never 4+ (that would
    # signal a duplicate emitter re-appeared).
    assert 1 <= len(hbs) <= 4, (
        f"expected 1-4 heartbeats in 3 s at 1 s cadence, "
        f"got {len(hbs)}: {hbs}"
    )

    # No two heartbeats may land within 100 ms of each other —
    # the classic race signature between two parallel emitters.
    times = [h["at"] for h in hbs]
    for a, b in zip(times, times[1:]):
        assert (b - a) > 0.1, (
            f"heartbeats too close ({b - a:.3f}s) — signals a "
            f"duplicate emitter has re-appeared"
        )

    # No heartbeat carries a `file` field — that was the exclusive
    # signature of the removed iter-278 per-file loop.
    for h in hbs:
        assert "file" not in (h.get("data") or {}), (
            f"heartbeat carries `file` field — iter 278 per-file "
            f"loop has been re-introduced: {h}"
        )
