"""
Iter 212m-112 — Tests for:
  - Auto-restart on phase timeout (LoopEngine._with_budget retries up
    to MAX_PHASE_RESTARTS times with exponential backoff before
    failing — mirrors the "thinking auto-restart" UX the founder
    asked for).
  - Realistic phase budgets (execute bumped from 120s → 300s, etc).
  - loop_execute.py parallel generation with bounded concurrency +
    PER-FILE timeout so one slow file can't blow the whole budget.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, "/app/backend")


# ─── 1. Phase budgets ─────────────────────────────────────────────────
def test_phase_budgets_realistic():
    from services.loop_engine import PHASE_TIMEOUTS_S
    # The user reported: "Phase execute exceeded 120s budget".
    # Execute must be at least 200s now that we run 6+ files in parallel
    # batches of 3 with per-file timeout 60s.
    assert PHASE_TIMEOUTS_S["execute"] >= 200
    assert PHASE_TIMEOUTS_S["verify"]  >= 120
    assert PHASE_TIMEOUTS_S["scan"]    >= 120
    assert PHASE_TIMEOUTS_S["ship"]    >= 60
    assert PHASE_TIMEOUTS_S["plan"]    >= 60


def test_max_phase_restarts_is_at_least_1():
    # Iter 309 · Phase 0.2 — Founder-approved reduction 2→1 (iter 131
    # RCA: verify-storm caused ~9 min of wasted work when the same
    # deterministic LLM call was retried twice against the same file
    # set with the same failure). One retry is sufficient for the
    # transient-flake bucket we're protecting against; a second retry
    # empirically never converted a failure to a success on non-
    # transient errors.
    from services.loop_engine import MAX_PHASE_RESTARTS
    assert MAX_PHASE_RESTARTS >= 1, \
        "Auto-restart must retry at least once before failing"


# ─── 2. Auto-restart logic ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_with_budget_retries_on_timeout_then_succeeds(monkeypatch):
    """A phase that times out once must auto-restart and succeed on
    the second attempt."""
    from services import loop_engine as le

    # Force tiny budget so the first attempt times out fast.
    monkeypatch.setattr(le, "PHASE_TIMEOUTS_S",
                        {**le.PHASE_TIMEOUTS_S, "execute": 0.1})
    monkeypatch.setattr(le, "MAX_PHASE_RESTARTS", 2)

    eng = le.LoopEngine.__new__(le.LoopEngine)
    eng.db          = None
    eng.loop_id     = "loop_t1"
    eng.user_id     = "u1"
    eng.project_id  = "p1"
    eng.state       = le.LoopState.IDLE
    eng.phase       = ""
    eng.context     = {"errors_encountered": []}

    emits: list[dict] = []
    async def fake_emit(state, phase, **kw):
        emits.append({"state": state.value if hasattr(state, "value") else state,
                      "phase": phase, **kw})
    eng._emit = fake_emit

    # Patch sleep so the test doesn't wait 2 seconds.
    real_sleep = asyncio.sleep
    async def fast_sleep(_t):
        await real_sleep(0)
    monkeypatch.setattr(le.asyncio, "sleep", fast_sleep)

    attempts = {"n": 0}
    async def flaky_phase():
        attempts["n"] += 1
        if attempts["n"] == 1:
            # First attempt hangs past the 0.1s budget.
            await real_sleep(0.5)
        # Second attempt returns immediately.
        return

    fail_calls: list[tuple] = []
    async def fake_fail(phase, reason):
        fail_calls.append((phase, reason))
    eng._fail = fake_fail

    await eng._with_budget("execute", flaky_phase)

    assert attempts["n"] == 2, "Must retry exactly once after first timeout"
    assert fail_calls == [], "Must NOT call _fail when retry succeeds"
    # An auto-restart event must have been emitted.
    restart_events = [e for e in emits if e.get("data", {}).get("kind") == "phase_auto_restart"]
    assert restart_events, "Must emit a phase_auto_restart event before retry"


@pytest.mark.asyncio
async def test_with_budget_fails_after_exhausting_restarts(monkeypatch):
    """If every retry also times out, _fail must be called exactly once."""
    from services import loop_engine as le

    monkeypatch.setattr(le, "PHASE_TIMEOUTS_S",
                        {**le.PHASE_TIMEOUTS_S, "execute": 0.05})
    monkeypatch.setattr(le, "MAX_PHASE_RESTARTS", 2)

    eng = le.LoopEngine.__new__(le.LoopEngine)
    eng.db          = None
    eng.loop_id     = "loop_t2"
    eng.user_id     = "u1"
    eng.project_id  = "p1"
    eng.state       = le.LoopState.IDLE
    eng.phase       = ""
    eng.context     = {"errors_encountered": []}
    eng._emit       = AsyncMock()

    real_sleep = asyncio.sleep
    async def fast_sleep(_t):
        await real_sleep(0)
    monkeypatch.setattr(le.asyncio, "sleep", fast_sleep)

    attempts = {"n": 0}
    async def always_slow():
        attempts["n"] += 1
        await real_sleep(0.5)

    fail_calls: list[tuple] = []
    async def fake_fail(phase, reason):
        fail_calls.append((phase, reason))
    eng._fail = fake_fail

    await eng._with_budget("execute", always_slow)

    # Initial attempt + MAX_PHASE_RESTARTS retries = 3 total.
    assert attempts["n"] == 3
    assert len(fail_calls) == 1
    assert fail_calls[0][0] == "execute"
    assert "budget" in fail_calls[0][1].lower()


# ─── 3. loop_execute parallel generation + per-file timeout ───────────
def test_loop_execute_uses_semaphore_and_per_file_timeout():
    src = open("/app/backend/services/loop_execute.py").read()
    # Must concurrent-execute with bounded concurrency.
    assert "asyncio.Semaphore" in src
    assert "asyncio.gather" in src
    # Per-file timeout MUST be enforced inside the worker.
    assert "asyncio.wait_for" in src
    assert "PER_FILE_TIMEOUT_S" in src
    # Default parallelism / per-file timeout knobs must exist.
    assert "MAX_PARALLEL_GENS" in src
    # Partial-success behaviour — single-file failure must NOT abort
    # the whole batch.
    assert "return None" in src


@pytest.mark.asyncio
async def test_generate_files_parallelism_under_timeout(monkeypatch):
    """6 files × 0.4 s LLM each should finish well under a 1.0 s wall
    clock when MAX_PARALLEL_GENS=3 — proves the fan-out works."""
    from services import loop_execute as ge

    # Force concurrency=3, per-file timeout 5s for the test.
    monkeypatch.setattr(ge, "MAX_PARALLEL_GENS", 3)
    monkeypatch.setattr(ge, "PER_FILE_TIMEOUT_S", 5)

    plan = {
        "title": "Test", "bullets": ["a", "b"],
        "files_to_change": [f"file{i}.py" for i in range(6)],
    }

    async def fake_fetch_file(client, owner, repo, path, token):
        return "# old\n"

    async def fake_llm(*, system, user, **kw):
        await asyncio.sleep(0.4)
        # Extract the file path from the user prompt for realistic output.
        return {"content": "# rewritten\nprint('hi')\n"}

    # Patch the dynamic imports inside _generate_one_inner.
    import services.github_api_writer as gw
    import services.llm as ll
    monkeypatch.setattr(gw, "fetch_file", fake_fetch_file)
    monkeypatch.setattr(ll, "call_llm_with_meta", fake_llm)

    import time
    t0 = time.time()
    out = await ge.generate_files(
        plan=plan, user_message="do it",
        owner="o", repo="r", branch="main", token="t",
        user_id="u1",
    )
    dt = time.time() - t0

    assert len(out) == 6, "All 6 files must succeed"
    assert all(o["content"] for o in out)
    # 6 files, parallelism 3, each 0.4s → ~0.8-1.0s. Hard-cap at 2s to
    # leave headroom for CI jitter but still prove concurrency.
    assert dt < 2.0, f"Expected parallel fan-out, took {dt:.2f}s"


@pytest.mark.asyncio
async def test_generate_files_one_slow_file_doesnt_kill_batch(monkeypatch):
    """One file that hangs past PER_FILE_TIMEOUT_S must be dropped
    (return None) — the rest of the batch still succeeds."""
    from services import loop_execute as ge

    monkeypatch.setattr(ge, "MAX_PARALLEL_GENS",   3)
    monkeypatch.setattr(ge, "PER_FILE_TIMEOUT_S", 1)  # tight timeout

    plan = {
        "title": "Test", "bullets": [],
        "files_to_change": ["fast1.py", "slow.py", "fast2.py"],
    }

    async def fake_fetch_file(client, owner, repo, path, token):
        return ""

    async def fake_llm(*, system, user, **kw):
        if "slow.py" in user:
            await asyncio.sleep(3)  # exceeds PER_FILE_TIMEOUT_S=1
        return {"content": "print(1)\n"}

    import services.github_api_writer as gw
    import services.llm as ll
    monkeypatch.setattr(gw, "fetch_file", fake_fetch_file)
    monkeypatch.setattr(ll, "call_llm_with_meta", fake_llm)

    out = await ge.generate_files(
        plan=plan, user_message="do it",
        owner="o", repo="r", branch="main", token="t",
    )
    paths = sorted([f["path"] for f in out])
    assert paths == ["fast1.py", "fast2.py"], \
        f"Slow file must drop out, got {paths}"


def test_loop_engine_with_budget_emits_auto_restart_event():
    src = open("/app/backend/services/loop_engine.py").read()
    # Verify the event payload uses the documented marker so the
    # frontend (LoopStepBar / self-heal indicator) can render it.
    assert '"kind":  "phase_auto_restart"' in src or '"kind": "phase_auto_restart"' in src
    assert "MAX_PHASE_RESTARTS" in src
