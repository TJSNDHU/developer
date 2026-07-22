"""
Iter 274 — Personal Track QA gates (T1.5 design review + T4 QA gate)
+ reused loop_verification_log audit trail with origin discrimination.

Tests 3 things end-to-end (real MongoDB, no LLM real calls needed for
these — the goal is to prove wiring/GC/hash-guard/origin, not to
re-prove GLM's reviewer behaviour which is covered by
`test_iter272_real_llm_verifier.py`):

  1. GC-safe background task registry — 20 concurrent bg tasks all
     complete AND write their row (proves the module-level set +
     add_done_callback pattern prevents the "task disappeared" bug).
  2. Stale verdict discarded on T3 race — if a design review lands
     AFTER `/regenerate` has swapped the draft's files, the write
     is a no-op (matched_count=0) and the fresher verdict stays.
  3. origin field is stamped on every write — both loop-mode
     `verify()` and scaffold `verify_scaffold()` set it; scanning
     recent rows finds none missing.

Every test round-trips through the real MONGO_URL from
backend/.env. LLM path is stubbed via monkeypatch so the tests
stay fast (real LLM path is covered elsewhere).
"""
from __future__ import annotations

import asyncio
import os
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

from services import (                                        # noqa: E402
    loop_task_specs as lts,
    loop_independent_verifier as liv,
    scaffold_design_review as sdr,
)
from routers import scaffold as sc                           # noqa: E402


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    d = client[os.environ["DB_NAME"]]
    for mod in (lts, liv):
        try:
            await mod.ensure_indexes(d)
        except Exception:
            pass
    yield d
    client.close()


@pytest.fixture
def unique():
    return "iter274_" + os.urandom(6).hex()


# ═══════════════════════════════════════════════════════════════
# Test 1 — GC-safe background task registry under concurrency
# ═══════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_bg_tasks_all_complete_under_concurrency(db, unique):
    """20 concurrent bg tasks must all write their row. Without
    the module-level set + callback, asyncio can drop weak-ref'd
    tasks mid-run under load."""
    N = 20
    fired: list[str] = []

    async def _work(i: int):
        # Small yield so scheduler interleaves them.
        await asyncio.sleep(0.001 * (i % 5))
        await db.iter274_bg_probe.insert_one(
            {"marker": unique, "i": i})
        fired.append(f"done-{i}")

    # Snapshot the registry so we can assert it drains cleanly.
    before = len(sc._BG_TASKS)
    tasks = [sc._spawn_bg(_work(i)) for i in range(N)]
    assert len(sc._BG_TASKS) - before == N, \
        "spawn should have added N strong references"

    # Wait for all to finish.
    await asyncio.gather(*tasks)

    assert len(fired) == N, f"only {len(fired)}/{N} tasks completed"
    written = await db.iter274_bg_probe.count_documents(
        {"marker": unique})
    assert written == N, f"only {written}/{N} rows in Mongo"

    # add_done_callback should have discarded all N tasks.
    await asyncio.sleep(0.05)   # let event loop process callbacks
    assert len(sc._BG_TASKS) == before, (
        f"registry did not drain — leaked {len(sc._BG_TASKS) - before} "
        f"strong refs")

    # Cleanup probe rows.
    await db.iter274_bg_probe.delete_many({"marker": unique})


# ═══════════════════════════════════════════════════════════════
# Test 2 — Stale verdict discarded when /regenerate races
# ═══════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_stale_verdict_dropped_on_regenerate_race(
        db, unique, monkeypatch):
    """Simulate: draft has files_v1 with hash_v1. Background review
    starts against v1. Before the review can write, /regenerate
    swaps files to v2, changing files_hash. The late v1 verdict
    must NOT overwrite the draft's design_review field."""
    draft_id = unique + "_stale"
    v1_files = [{"path": "index.html", "content": "<h1>v1</h1>"}]
    v2_files = [{"path": "index.html", "content": "<h1>v2</h1>"}]
    v1_hash = sc._compute_files_hash(v1_files)
    v2_hash = sc._compute_files_hash(v2_files)
    assert v1_hash != v2_hash

    # Seed draft at v1.
    await db.scaffold_drafts.insert_one({
        "draft_id":     draft_id,
        "user_id":      "u_test",
        "brief":        "test",
        "files":        v1_files,
        "files_hash":   v1_hash,
        "status":       "draft",
        "created_at":   0,
        "updated_at":   0,
        "design_review": None,
    })

    # Stub verify_scaffold to return a slow verdict — gives us time
    # to swap the draft to v2 mid-flight.
    async def slow_v1_verify(db_, *, draft_id, brief, files,
                               reviewer_model=None):
        await asyncio.sleep(0.15)
        return {
            "loop_id":        draft_id,
            "verifier_model": "stub",
            "verdict":        "no",
            "reason":         "stale_v1_verdict_should_be_dropped",
            "user_message":   "stale — must not land",
            "latency_s":      0.15,
            "created_at":     "2026-07-22T00:00:00+00:00",
            "raw":             "",
            "origin":          "personal_track",
        }
    # Patch the symbol AT the routers.scaffold import site (that's
    # what _run_design_review_bg actually calls).
    import services.scaffold_design_review as _sdr_mod
    monkeypatch.setattr(_sdr_mod, "verify_scaffold", slow_v1_verify)

    # Fire the (slow) bg review for v1.
    task = sc._spawn_bg(sc._run_design_review_bg(
        draft_id, "u_test", "test", v1_files, v1_hash,
    ))

    # Race: swap the draft to v2 BEFORE the slow verify returns.
    await asyncio.sleep(0.05)
    await db.scaffold_drafts.update_one(
        {"draft_id": draft_id, "user_id": "u_test"},
        {"$set": {"files": v2_files, "files_hash": v2_hash,
                  "design_review": None}},
    )

    # Now wait for the stale v1 task to finish.
    await task

    doc = await db.scaffold_drafts.find_one({"draft_id": draft_id})
    # The predicate {files_hash: v1_hash} no longer matches, so the
    # v1 review write should be a no-op → design_review stays None.
    assert doc["files_hash"] == v2_hash
    assert doc["design_review"] is None, (
        f"stale v1 verdict LEAKED onto v2 draft: {doc['design_review']!r}")

    # Cleanup.
    await db.scaffold_drafts.delete_one({"draft_id": draft_id})


# ═══════════════════════════════════════════════════════════════
# Test 3 — origin field is present on every write (both modes)
# ═══════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_origin_field_never_missing_after_backfill(
        db, unique, monkeypatch):
    """Freeze a spec, then simulate one loop-mode verify + one
    scaffold verify with the LLM stubbed. Both rows must have
    origin set. Then run backfill_origin() and assert NO row in
    loop_verification_log is missing origin — this is the
    permanent invariant."""
    # Loop-mode row (origin="loop_mode" default).
    loop_id = unique + "_loop"
    await lts.freeze(
        db, loop_id=loop_id, task_id=None, user_id="u",
        project_id="p", user_message="Add a health check",
        plan="- Add /api/health route\n- Return 200 {ok:true}",
    )
    async def fake_shot(**_):
        return ('{"verdict":"yes","reason":"looks good"}', {}, None)
    monkeypatch.setattr(liv, "_one_shot", fake_shot)
    row_loop = await liv.verify(
        db, loop_id=loop_id,
        files=[{"path": "backend/routers/health.py", "content": "x"}],
        verifier_model="stub",
    )
    assert row_loop["origin"] == "loop_mode"

    # Scaffold-mode row (origin="personal_track").
    draft_id = unique + "_pt"
    async def fake_shot_scaffold(**_):
        return ('{"verdict":"yes","technical_reason":"ok","user_message":"Looks good."}', {}, None)
    monkeypatch.setattr(sdr, "_one_shot", fake_shot_scaffold)
    row_pt = await sdr.verify_scaffold(
        db, draft_id=draft_id,
        brief="todo app with login",
        files=[{"path": "src/App.jsx", "content": "..."}],
        reviewer_model="stub",
    )
    assert row_pt["origin"] == "personal_track"

    # Run the backfill (should be a no-op if writers are correct,
    # but MUST still return before/after counts).
    stats = await liv.backfill_origin(db)
    assert stats["after"] == 0, (
        f"invariant violated — {stats['after']} rows STILL missing "
        f"origin after backfill: {stats}")

    # Full-collection invariant: nothing in loop_verification_log
    # may be missing origin.
    missing = await db.loop_verification_log.count_documents(
        {"origin": {"$exists": False}})
    assert missing == 0, (
        f"{missing} rows have no origin field — invariant broken")

    # Cleanup our two test rows.
    await db.loop_verification_log.delete_many(
        {"loop_id": {"$in": [loop_id, draft_id]}})
    await db.loop_task_specs.delete_one({"loop_id": loop_id})
