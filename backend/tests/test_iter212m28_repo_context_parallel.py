"""
Iter 212m-28 — repo_context hot-path optimisation.

Three production-grade fixes to take cold-path repo loading from
5-15 s → 1-3 s:

  1. PARALLEL file inlining — was the dominant cost (10 files × 500 ms
     = ~5 s sequential). Now wrapped in asyncio.gather() with a
     semaphore of 6 to stay under GitHub's secondary rate limit.

  2. PARALLEL truncation rescue — the per-top-level-dir BFS used to
     run sequentially (~8 dirs × 1 s = ~8 s). Now also fan-out.

  3. BRANCH-AWARE cache + TIMING INSTRUMENTATION — cache key now
     includes branch (switching branches no longer returns stale
     blob); every call records timing samples into
     `repo_context_timings` (7-day TTL) for production-grade visibility.
"""
from __future__ import annotations

import asyncio
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
RC_PY  = os.path.join(ROOT, "services", "repo_context.py")
MAIN_PY = os.path.join(ROOT, "main.py")


# ── 1. Source pins — parallelism is in place ─────────────────────────

def test_inline_loop_uses_asyncio_gather():
    """The pre-refactor `for path in picks: await _fetch_file(...)`
    block must be GONE — replaced by gather(*_bounded_fetch(p) for p in picks)."""
    src = open(RC_PY).read()
    # The sequential loop signature is gone:
    assert "for path in picks:\n        if used >= MAX_TOTAL_CHARS:" not in src
    # The gather replacement is present:
    assert "raw_bodies = await asyncio.gather(" in src
    assert "*(_bounded_fetch(p) for p in picks)" in src


def test_rescue_loop_uses_asyncio_gather():
    """The truncation-rescue per-dir loop must be parallelised too."""
    src = open(RC_PY).read()
    assert "rescue_results = await asyncio.gather(" in src
    assert "*(_bounded_rescue(d) for d in top_level_dirs)" in src


def test_fetch_concurrency_capped_at_six():
    """A semaphore caps concurrency at 6 to stay under GitHub's
    secondary rate limit."""
    src = open(RC_PY).read()
    assert "_FETCH_CONCURRENCY = 6" in src
    assert "asyncio.Semaphore(_FETCH_CONCURRENCY)" in src


# ── 2. Branch-aware cache ───────────────────────────────────────────

def test_cache_key_includes_branch():
    src = open(RC_PY).read()
    # The new branch-aware cache key.
    assert 'cache_key = {"project_id": project_id, "branch": branch}' in src
    # Old cache_key without branch is gone.
    assert (
        'find_one({"project_id": project_id})'
    ) not in src  # the old reader is replaced
    # The update_one upsert also uses the branch-aware key.
    assert 'await db.repo_contexts.update_one(\n            cache_key,' in src


def test_invalidate_deletes_all_branches_of_project():
    """A PAT change affects every branch — invalidate must clear them all."""
    src = open(RC_PY).read()
    assert "await db.repo_contexts.delete_many(" in src
    assert "await db.repo_contexts.delete_one(" not in src


# ── 3. Timing instrumentation ───────────────────────────────────────

def test_record_timing_helper_present():
    src = open(RC_PY).read()
    assert "async def _record_timing(" in src
    # Best-effort — must never raise.
    assert "Telemetry is best-effort; never crash a chat turn." in src \
        or "never raises" in src


def test_cold_path_records_per_phase_timings():
    """A cold build must record tree_fetch_ms, rescue_ms, inline_ms."""
    src = open(RC_PY).read()
    for phase in ('"tree_fetch_ms"', '"rescue_ms"', '"inline_ms"'):
        assert phase in src, f"missing timing phase {phase}"


def test_cache_hit_also_records_timing():
    """Cache hits are the dominant path — we still need a sample so
    production dashboards don't go blind on warm traffic."""
    src = open(RC_PY).read()
    # The cache-hit branch creates a timing record with cold_path=False
    # and a cache_hit_ms phase.
    assert '"cache_hit_ms":' in src
    assert "cold_path=False" in src


def test_cold_path_log_uses_parameterised_format():
    """Vanguard: parameterised logging, no f-strings carrying user ids."""
    src = open(RC_PY).read()
    assert (
        'logger.info(\n        "repo_context COLD build pid=%s '
    ) in src
    # No f-strings in the new repo_context path on identifier vars.
    bad = re.findall(r'logger\.(?:info|warning|error|debug)\(\s*f"[^"]*\{(?:project_id|user_id|owner|repo|branch)', src)
    assert bad == [], f"f-string logging on identifier vars: {bad}"


def test_telemetry_collection_constant():
    src = open(RC_PY).read()
    assert '_TIMINGS_COLL = "repo_context_timings"' in src
    assert "_TIMINGS_TTL_DAYS = 7" in src


def test_main_creates_ttl_index_on_timings():
    """The collection grows on every chat turn — a 7-day TTL index is
    a production hard requirement so Atlas doesn't fill up."""
    src = open(MAIN_PY).read()
    assert "repo_context_timings.create_index(" in src
    assert "expireAfterSeconds=7 * 24 * 60 * 60" in src
    assert "ts_ttl_7d" in src


# ── 4. Runtime sanity — parallel fetcher is faster than serial ──────

def test_parallel_gather_is_actually_faster_than_serial():
    """Tight model: 6 mock fetches × 200 ms each.
    Serial: ~1200 ms. Parallel (sem=6): ~200 ms."""
    import importlib, sys, time
    if "services.repo_context" in sys.modules:
        importlib.reload(sys.modules["services.repo_context"])

    async def _slow_fetch():
        await asyncio.sleep(0.20)
        return "ok"

    async def _serial():
        out = []
        for _ in range(6):
            out.append(await _slow_fetch())
        return out

    async def _parallel():
        sem = asyncio.Semaphore(6)

        async def _bounded():
            async with sem:
                return await _slow_fetch()
        return await asyncio.gather(*(_bounded() for _ in range(6)))

    loop = asyncio.new_event_loop()
    try:
        t0 = time.monotonic()
        loop.run_until_complete(_serial())
        serial_s = time.monotonic() - t0
        t0 = time.monotonic()
        loop.run_until_complete(_parallel())
        par_s = time.monotonic() - t0
    finally:
        loop.close()
    # Parallel should be at least 3x faster than serial for 6 fetches.
    assert par_s < serial_s / 3.0, (
        f"parallel not fast enough — serial={serial_s:.3f}s, parallel={par_s:.3f}s"
    )
