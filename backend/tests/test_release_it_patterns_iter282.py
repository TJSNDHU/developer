"""
Iter 282 — Release It! patterns audit (Bulkhead / Steady State / Governor)

Three permanent regression tests + fitness invariants that lock in
the audit findings.
"""
from __future__ import annotations
import os
import time
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")


@pytest_asyncio.fixture
async def db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"],
                           serverSelectionTimeoutMS=3000)
    yield c[os.environ["DB_NAME"]]
    c.close()


# ═══════════════════════════════════════════════════════════════════
# BULKHEAD — project-level isolation
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_regression_iter282_bulkhead_project_isolation(db):
    """
    Two users hitting the SAME project concurrently must each get
    their own lock — one user's stuck loop does NOT block the other.
    The unique index is on {project_id, user_id} exactly for this.
    """
    from services.loop_safety import acquire_loop_lock, release_loop_lock

    proj = f"bulkhead-{int(time.time()*1000)}"
    user_a = f"user-A-{int(time.time()*1000)}"
    user_b = f"user-B-{int(time.time()*1000)}"

    ok_a, _ = await acquire_loop_lock(db, proj, user_a, "loop-A")
    assert ok_a, "user A acquires lock on shared project"

    # User B on the SAME project must ALSO be able to acquire.
    ok_b, _ = await acquire_loop_lock(db, proj, user_b, "loop-B")
    assert ok_b, (
        "user B must be able to run a loop on the same project — "
        "one user's stuck loop cannot block another."
    )

    # But a SECOND acquire by user A on same project must be refused.
    ok_a2, existing = await acquire_loop_lock(db, proj, user_a, "loop-A2")
    assert not ok_a2, "user A cannot start a 2nd concurrent loop on same project"
    assert existing.get("loop_id") == "loop-A"

    await release_loop_lock(db, proj, user_a, "loop-A")
    await release_loop_lock(db, proj, user_b, "loop-B")


def test_invariant_bulkhead_unique_index_declared():
    """
    Source-level: the `loop_locks` unique index in
    init_prod_collections.py must be scoped to
    {project_id, user_id}. Widening this to just {project_id}
    would break Bulkhead (one user's stuck loop blocks all users
    on the same project) — CI must catch that.
    """
    src = open("/app/backend/scripts/init_prod_collections.py").read()
    # Find the loop_locks block.
    idx = src.find('"loop_locks"')
    assert idx > -1, "loop_locks block must exist"
    block = src[idx: idx + 400]
    assert '("project_id", 1), ("user_id", 1)' in block, (
        "loop_locks unique index MUST be composite "
        "{project_id, user_id} — Bulkhead pattern requires per-user "
        "isolation within a shared project."
    )
    assert '"unique": True' in block, (
        "loop_locks {project_id, user_id} index must be UNIQUE"
    )


# ═══════════════════════════════════════════════════════════════════
# STEADY STATE — TTL on accumulating collections
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_invariant_loop_collections_have_ttl_indexes(db):
    """
    All 6 loop-machinery collections must have a TTL index. Without
    it they grow monotonically forever — the very Steady State
    anti-pattern Release It! warns about.

    Retention tiers (documented in init_prod_collections.py):
      • 7 d   — loop_events, loop_locks, loop_failures  (ephemeral)
      • 30 d  — loop_sessions                            (audit)
      • 90 d  — loop_verification_log, loop_run_log      (analytics)
    """
    expected = {
        "loop_events":            (7,  "created_at"),
        "loop_locks":             (7,  "acquired_at"),
        "loop_failures":          (7,  "occurred_at"),
        "loop_sessions":          (30, "updated_at"),
        "loop_verification_log":  (90, "created_at"),
        "loop_run_log":           (90, "created_at"),
    }
    for coll, (days, field) in expected.items():
        idxs = await db[coll].index_information()
        ttl_idx = [
            (name, info) for name, info in idxs.items()
            if "expireAfterSeconds" in info
        ]
        assert ttl_idx, f"{coll} must have a TTL index (Steady State)"
        # At least one TTL index must be on the expected field with
        # the expected retention.
        matching = [
            (n, i) for n, i in ttl_idx
            if any(k == field for k, _ in i.get("key", []))
            and i["expireAfterSeconds"] == days * 24 * 3600
        ]
        assert matching, (
            f"{coll} TTL index must be on `{field}` with "
            f"expireAfterSeconds={days*24*3600} (got {ttl_idx})"
        )


def test_invariant_steady_state_declared_in_bootstrap_spec():
    """
    init_prod_collections.py must declare TTL for all 6 loop
    collections. New deploys go through this script.
    """
    src = open("/app/backend/scripts/init_prod_collections.py").read()
    required = [
        "loop_events", "loop_locks", "loop_failures",
        "loop_sessions", "loop_verification_log", "loop_run_log",
    ]
    for coll in required:
        assert f'"{coll}"' in src, (
            f"{coll} must be declared in _BOOTSTRAP_SPEC (Steady State)"
        )
    # And each must have an expireAfterSeconds line nearby.
    for coll in required:
        idx = src.find(f'"{coll}"')
        block = src[idx: idx + 500]
        assert "expireAfterSeconds" in block, (
            f"{coll} block must include expireAfterSeconds — no TTL "
            f"= Steady State violation"
        )


# ═══════════════════════════════════════════════════════════════════
# GOVERNOR — retry / stream ceilings
# ═══════════════════════════════════════════════════════════════════
def test_regression_iter282_sse_stream_has_wallclock_ceiling():
    """
    The `while True` in routers/loop.py::stream_loop must break out
    on a wall-clock ceiling. Without this, a stuck non-terminal loop
    keeps the SSE generator alive indefinitely, tying up an app
    worker — classic no-Governor failure mode.
    """
    src = open("/app/backend/routers/loop.py").read()

    assert "_STREAM_MAX_S" in src, (
        "routers/loop.py must declare an SSE stream wall-clock cap "
        "(Release It! Governor pattern)."
    )
    # And the cap must actually be enforced inside the while loop.
    # Heuristic: the check must appear inside the generator, before
    # the ev = None line the SSE loop starts with.
    gen_idx = src.find("async def gen():")
    assert gen_idx > -1
    ev_none_idx = src.find("ev = None", gen_idx)
    # Body between gen() and the first ev = None must contain the cap.
    body = src[gen_idx: ev_none_idx]
    assert "time.monotonic()" in body, (
        "gen() must sample time.monotonic() before ev = None"
    )
    assert "_STREAM_MAX_S" in body, (
        "gen() must check the ceiling every iteration"
    )


def test_invariant_governor_retry_ceilings_exist():
    """
    Every module that does external I/O with retries must expose a
    NAMED ceiling constant. This test doesn't enforce EVERY retry
    loop has one (that's Phase-3 watcher territory) — it locks the
    known-critical ones so a silent removal fails CI.
    """
    known = [
        ("services/llm.py",         "_MAX_RETRIES"),
        ("services/loop_engine.py", "MAX_SELF_HEALS"),
        ("services/loop_engine.py", "MAX_PHASE_RESTARTS"),
        ("services/loop_safety.py", "max_retries"),
    ]
    for path, sym in known:
        full = f"/app/backend/{path}"
        src = open(full).read()
        assert sym in src, (
            f"{path} must expose `{sym}` (Governor retry ceiling). "
            "Its removal means an unbounded retry loop can silently "
            "return."
        )
