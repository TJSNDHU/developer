"""
Iter 212m-145 — Ghost loop_lock release after cancel + auto-sweep
for terminated loops on acquire.

LIVE PROD REPRO (Feb 2026 founder stress test):
  • A loop FAILED at the verify phase.
  • User called POST /loop/{id}/cancel → returned `state: aborted`.
  • User called GET /loop/active → returned `loop: null` (no active).
  • User called POST /loop/start on the SAME project → 409
    `loop_already_running existing_loop_id=loop_f6bb2a023f7449`.
  • The loop was clearly NOT running, but the `loop_locks` Mongo row
    was never deleted — the engine's cancel() path only releases the
    lock when the engine is in this worker's `_LIVE`. With multiple
    PROD workers + the engine never being in this worker's _LIVE
    after the original run, the cancel router's fallback path
    persisted `state=aborted` to `loop_sessions` but didn't touch
    `loop_locks`, leaving the user locked-out of their own project
    for 15 minutes (the stale_s timeout).

REAL FIX (both layers, no patches):

  A. `routers/loop.py` cancel endpoint fallback — when the engine
     can't be rehydrated (terminal state) but the session doc exists,
     also call `release_loop_lock(...)` so the project isn't held
     captive by a ghost lock.

  B. `services/loop_safety.acquire_loop_lock` — proactive ghost
     sweep. Before falling back to "loop_already_running", check
     whether the lock's `loop_id` already shows a terminal state in
     `loop_sessions`. If so, sweep the lock and proceed with the new
     claim. This is defence-in-depth: even if a worker crashes
     mid-flight WITHOUT either cancel-fallback OR engine.cancel()
     running, the next /start request auto-recovers within 1 round
     trip — no 15-min wait for stale_s.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import loop_safety as ls


pytestmark = pytest.mark.asyncio


class _Coll:
    def __init__(self, docs):
        self.docs = list(docs)
        self.deleted: list[dict] = []
        self.inserted: list[dict] = []

    async def find_one(self, filt, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()
                   if not isinstance(v, dict)):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    async def insert_one(self, doc):
        # Mimic the unique-index conflict: if a doc with same
        # project_id+user_id exists, raise.
        for d in self.docs:
            if (d.get("project_id") == doc.get("project_id")
                    and d.get("user_id") == doc.get("user_id")):
                raise RuntimeError("E11000 duplicate key")
        self.inserted.append(doc)
        self.docs.append(doc)
        return SimpleNamespace(inserted_id="x")

    async def delete_one(self, filt):
        before = len(self.docs)
        self.docs = [
            d for d in self.docs
            if not all(d.get(k) == v for k, v in filt.items())
        ]
        self.deleted.append(filt)
        return SimpleNamespace(deleted_count=before - len(self.docs))

    async def delete_many(self, filt):
        # Stale sweep — match `acquired_at: {$lt: ...}` etc.
        threshold = None
        if isinstance(filt.get("acquired_at"), dict):
            threshold = filt["acquired_at"].get("$lt")
        keep = []
        deleted = 0
        for d in self.docs:
            same = (d.get("project_id") == filt.get("project_id")
                    and d.get("user_id") == filt.get("user_id"))
            stale = (threshold is not None
                     and d.get("acquired_at", 0) < threshold)
            if same and stale:
                deleted += 1
                continue
            keep.append(d)
        self.docs = keep
        return SimpleNamespace(deleted_count=deleted)


class _FakeDB:
    def __init__(self, locks, sessions=None):
        self.loop_locks = _Coll(locks)
        self.loop_sessions = _Coll(sessions or [])


async def test_acquire_sweeps_ghost_lock_for_aborted_loop():
    """Exact PROD repro: a stale lock points to a loop that's already
    in `state: aborted` in loop_sessions. acquire_loop_lock must
    detect it, sweep the lock, then claim the new one."""
    now = time.time()
    db = _FakeDB(
        locks=[{
            "project_id": "p1", "user_id": "u1",
            "loop_id": "loop_ghost", "acquired_at": now - 60,
        }],
        sessions=[{
            "loop_id": "loop_ghost", "state": "aborted",
        }],
    )
    ok, existing = await ls.acquire_loop_lock(
        db, "p1", "u1", "loop_new",
    )
    assert ok is True
    assert existing is None
    # New lock for loop_new should now be present.
    assert any(d["loop_id"] == "loop_new" for d in db.loop_locks.docs)
    assert not any(d["loop_id"] == "loop_ghost" for d in db.loop_locks.docs)


async def test_acquire_sweeps_ghost_lock_for_failed_loop():
    """Same logic for state=failed."""
    now = time.time()
    db = _FakeDB(
        locks=[{
            "project_id": "p1", "user_id": "u1",
            "loop_id": "loop_dead", "acquired_at": now - 60,
        }],
        sessions=[{
            "loop_id": "loop_dead", "state": "failed",
        }],
    )
    ok, existing = await ls.acquire_loop_lock(db, "p1", "u1", "loop_new")
    assert ok is True
    assert any(d["loop_id"] == "loop_new" for d in db.loop_locks.docs)


async def test_acquire_sweeps_ghost_lock_for_completed_loop():
    """And state=completed."""
    now = time.time()
    db = _FakeDB(
        locks=[{
            "project_id": "p1", "user_id": "u1",
            "loop_id": "loop_done", "acquired_at": now - 60,
        }],
        sessions=[{
            "loop_id": "loop_done", "state": "completed",
        }],
    )
    ok, existing = await ls.acquire_loop_lock(db, "p1", "u1", "loop_new")
    assert ok is True


async def test_acquire_keeps_lock_for_running_loop():
    """Sanity: an ACTUAL running loop must still block new starts."""
    now = time.time()
    db = _FakeDB(
        locks=[{
            "project_id": "p1", "user_id": "u1",
            "loop_id": "loop_running", "acquired_at": now - 30,
        }],
        sessions=[{
            "loop_id": "loop_running", "state": "executing",
        }],
    )
    ok, existing = await ls.acquire_loop_lock(db, "p1", "u1", "loop_new")
    assert ok is False
    assert existing is not None
    assert existing["loop_id"] == "loop_running"


async def test_acquire_keeps_lock_when_session_missing():
    """If session lookup returns None (e.g. mongo lag) we err on the
    side of caution and DO NOT sweep — better a false-positive lock
    than a split-brain double-execute."""
    now = time.time()
    db = _FakeDB(
        locks=[{
            "project_id": "p1", "user_id": "u1",
            "loop_id": "loop_unknown", "acquired_at": now - 30,
        }],
        sessions=[],  # NO session record
    )
    ok, existing = await ls.acquire_loop_lock(db, "p1", "u1", "loop_new")
    assert ok is False
    assert existing["loop_id"] == "loop_unknown"


async def test_router_cancel_fallback_releases_lock():
    """Source-pattern contract: routers/loop.py cancel fallback path
    must call `release_loop_lock` when persisting state=aborted via
    Mongo (i.e. when engine couldn't be rehydrated)."""
    from pathlib import Path
    src = Path("/app/backend/routers/loop.py").read_text(encoding="utf-8")
    # Marker present so future agents know why the fallback does the
    # extra release call.
    assert "Iter 212m-145" in src
    # Must call release_loop_lock from the fallback path.
    assert "from services.loop_safety import release_loop_lock" in src
    assert "release_loop_lock(" in src


async def test_force_release_lock_endpoint_exists():
    """Iter 212m-146 — Founder safety hatch must exist."""
    from pathlib import Path
    src = Path("/app/backend/routers/loop.py").read_text(encoding="utf-8")
    assert 'router.post("/force-release-lock")' in src
    assert "founder access required" in src
    assert "Iter 212m-146" in src
