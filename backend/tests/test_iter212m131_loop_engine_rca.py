"""
Iter 212m-131 — Loop Engine deep-RCA fixes.

Eleven root-cause fixes from the verbatim RCA in PRD.md:

  #1  asyncio.create_task ref held on self._pipeline_task
  #2  Verify storm — internal retries × per-file LLM > budget; the
      new design does ONE pass + up to MAX_SELF_HEALS heal rounds,
      and only re-verifies files that were healed (not the ones
      already passing).
  #3  MAX_VERIFY_RETRIES coincidence removed — single MAX_SELF_HEALS
  #4  self_heal() LLM call wrapped in wait_for(SELF_HEAL_LLM_TIMEOUT_S)
  #5  verify_files lints CONCURRENTLY with a semaphore (cap=4)
  #6  _do_execute on empty plan now _fail()s instead of silently
      progressing through Verify → Scan → Ship doing nothing
  #7  _with_budget restart now clears phase-specific context keys
  #8  cancel() actually task.cancel()s the pipeline + releases lock
  #9  submit_files() rejected once engine is past AWAITING_CONFIRMATION
  #10 _with_budget restart sets state BEFORE emitting SELF_HEALING
  #11 NEW MAX_PHASE_RESTARTS = 1 (was 2) to bound worst-case wall
      time at 2× budget

This file tests the FIXES directly without spinning up the full
chat/SSE stack — we instantiate LoopEngine with a fake db and drive
its methods, asserting the invariants the RCA described.
"""
from __future__ import annotations

import asyncio
import time

import pytest


# ──────────────────────────────────────────────────────────────────
# Lightweight Mongo + LLM doubles.
# ──────────────────────────────────────────────────────────────────
class _Coll:
    def __init__(self):
        self.rows: list[dict] = []
    async def insert_one(self, d):
        self.rows.append(dict(d))
        class _R:
            inserted_id = "x"
        return _R()
    async def update_one(self, q, u, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                for k, v in (u.get("$set") or {}).items():
                    r[k] = v
                class _R:
                    modified_count = 1
                    upserted_id = None
                return _R()
        if upsert:
            doc = {**q, **(u.get("$set") or {})}
            self.rows.append(doc)
        class _R:
            modified_count = 0
            upserted_id = "x" if upsert else None
        return _R()
    async def find_one(self, q, *_a, **_kw):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                return dict(r)
        return None
    async def delete_one(self, q):
        for i, r in enumerate(list(self.rows)):
            if all(r.get(k) == v for k, v in q.items()):
                self.rows.pop(i)
                class _R:
                    deleted_count = 1
                return _R()
        class _R:
            deleted_count = 0
        return _R()


class _DB:
    def __init__(self):
        self.loop_sessions    = _Coll()
        self.loop_backups     = _Coll()
        self.loop_plans       = _Coll()
        self.loop_lock        = _Coll()
        self.loop_failures    = _Coll()


def _make_engine(db=None):
    """Build a bare engine for state-machine testing.  We never call
    _run_pipeline()'s outer wrapper directly — individual methods
    are tested in isolation."""
    from services import loop_engine as le
    db = db or _DB()
    return le.LoopEngine(
        db=db, loop_id="lp_test", user_id="u1",
        project_id="p1", user_message="ship me a feature",
    )


# ──────────────────────────────────────────────────────────────────
# Bug #1 — pipeline task reference is held on self
# ──────────────────────────────────────────────────────────────────
def test_pipeline_task_ref_held_on_engine(monkeypatch):
    """confirm() must NOT drop the create_task() return value — the
    docs explicitly warn that a long-running orphan task can be GC'd."""
    from services import loop_engine as le

    eng = _make_engine()
    eng.state = le.LoopState.AWAITING_CONFIRMATION
    plan_dict = {"title": "t", "bullets": ["b1"], "files_to_change": []}
    # Pre-populate the plan so confirm() doesn't hit Mongo for it.
    eng.context["plan"] = plan_dict

    # Stub _run_pipeline so we just sleep — we're asserting the ref.
    async def fake_pipeline():
        await asyncio.sleep(0.5)
    monkeypatch.setattr(eng, "_run_pipeline", fake_pipeline)

    async def go():
        await eng.confirm(True)
        assert eng._pipeline_task is not None
        assert isinstance(eng._pipeline_task, asyncio.Task)
        assert not eng._pipeline_task.done()
        # Wait for completion → done_callback clears the ref.
        await eng._pipeline_task
        assert eng._pipeline_task is None

    asyncio.run(go())


# ──────────────────────────────────────────────────────────────────
# Bug #8 — cancel() must task.cancel() the pipeline
# ──────────────────────────────────────────────────────────────────
def test_cancel_propagates_to_pipeline_task(monkeypatch):
    from services import loop_engine as le

    eng = _make_engine()
    eng.state = le.LoopState.EXECUTING
    # Plant a fake long-running task so cancel() has something to act on.
    sleeper_started = asyncio.Event()
    async def slow():
        try:
            sleeper_started.set()
            await asyncio.sleep(60)   # would hang the test if not cancelled
        except asyncio.CancelledError:
            raise

    async def go():
        eng._pipeline_task = asyncio.create_task(slow())
        await sleeper_started.wait()
        await eng.cancel()
        # cancel() does NOT await the task itself (would block the HTTP
        # handler), but it must have requested cancellation.  Yield
        # once so the event loop processes the cancellation request.
        await asyncio.sleep(0.05)
        # Drain any leftover Cancelled state so pytest doesn't warn.
        try:
            await eng._pipeline_task
        except (asyncio.CancelledError, Exception):
            pass
        assert eng._cancelled is True
        assert eng.state == le.LoopState.ABORTED

    asyncio.run(go())


# ──────────────────────────────────────────────────────────────────
# Bug #2 + #3 + #4 — Verify storm killed; single source of truth
# ──────────────────────────────────────────────────────────────────
def test_verify_runs_max_self_heals_not_max_verify_retries():
    """Loops up to MAX_SELF_HEALS rounds, not MAX_VERIFY_RETRIES."""
    from services import loop_engine as le
    # Single source of truth — MAX_VERIFY_RETRIES is GONE from the
    # module namespace because the storm couldn't happen with one
    # constant.  This regression-pins the rename.
    assert not hasattr(le, "MAX_VERIFY_RETRIES")
    assert hasattr(le, "MAX_SELF_HEALS")
    assert le.MAX_SELF_HEALS == 2


def test_self_heal_timeout_constant_exists():
    """Bug #4 — a self_heal LLM call without a timeout could hang
    the verify phase indefinitely.  We pin the timeout constant so a
    refactor can't silently remove it."""
    from services import loop_engine as le
    assert hasattr(le, "SELF_HEAL_LLM_TIMEOUT_S")
    assert 30 <= le.SELF_HEAL_LLM_TIMEOUT_S <= 120


def test_max_phase_restarts_lowered_to_one():
    """Bug #11 — phase restarts capped at 1 (was 2).  Phase coros
    aren't idempotent across restarts; doing them 3 times before
    _fail() just burned 3× the budget for no real shot at success."""
    from services import loop_engine as le
    assert le.MAX_PHASE_RESTARTS == 1


def test_verify_only_re_lints_healed_files(monkeypatch):
    """Bug #2 — on the 2nd heal round, only the files that failed
    in round 1 are re-linted, not every file.  This is the single
    biggest contributor to killing the verify storm."""
    from services import loop_engine as le

    eng = _make_engine()
    eng.context["submitted_files"] = [
        {"path": "a.py", "content": "good = 1"},
        {"path": "b.py", "content": "bad = "},
        {"path": "c.py", "content": "ok = 2"},
    ]

    verify_calls: list[list[str]] = []
    async def fake_verify(files):
        verify_calls.append([f["path"] for f in files])
        # First call: a + c pass, b fails.
        # Subsequent calls (subset reverify): all pass.
        if len(verify_calls) == 1:
            return {
                "ok": False,
                "results": [
                    {"path": "a.py", "ok": True,  "linter": "ruff",
                     "stdout": "", "stderr": ""},
                    {"path": "b.py", "ok": False, "linter": "ruff",
                     "stdout": "b.py:1:7: SyntaxError",
                     "stderr": ""},
                    {"path": "c.py", "ok": True,  "linter": "ruff",
                     "stdout": "", "stderr": ""},
                ],
                "errors": ["b.py:1:7: SyntaxError"],
            }
        # subset (just b.py)
        return {
            "ok": True,
            "results": [{"path": "b.py", "ok": True, "linter": "ruff",
                         "stdout": "", "stderr": ""}],
            "errors": [],
        }

    async def fake_self_heal(file_obj, *_a, **_kw):
        return "bad = 1   # fixed\n"

    # Patch the imports `_do_verify` does.
    import services.loop_verify as lv
    monkeypatch.setattr(lv, "verify_files", fake_verify)
    monkeypatch.setattr(lv, "self_heal",   fake_self_heal)

    async def go():
        # Drive only the verify phase — skip the budget wrapper so we
        # can assert the inner contract.
        await eng._do_verify()

    asyncio.run(go())
    # 2 verify calls: full batch first, then ONLY b.py.
    assert len(verify_calls) == 2
    assert verify_calls[0] == ["a.py", "b.py", "c.py"]
    assert verify_calls[1] == ["b.py"]   # ← bug #2 fix
    assert eng.context["verification_results"]["ok"] is True


def test_verify_self_heal_timeout_doesnt_hang(monkeypatch):
    """Bug #4 — even a self_heal that NEVER returns must be killed
    by the SELF_HEAL_LLM_TIMEOUT_S guard so the verify phase can
    surface the failure instead of hanging until the outer budget."""
    from services import loop_engine as le

    # Temporarily shrink the timeout so the test runs fast.
    monkeypatch.setattr(le, "SELF_HEAL_LLM_TIMEOUT_S", 1)

    eng = _make_engine()
    eng.context["submitted_files"] = [
        {"path": "bad.py", "content": "broken = "},
    ]

    async def fake_verify(files):
        return {
            "ok": False,
            "results": [{"path": "bad.py", "ok": False, "linter": "ruff",
                         "stdout": "bad.py:1:7: SyntaxError",
                         "stderr": ""}],
            "errors": ["bad.py:1:7: SyntaxError"],
        }
    async def hanging_self_heal(*_a, **_kw):
        await asyncio.sleep(60)  # never finishes
        return "should-never-return"

    import services.loop_verify as lv
    monkeypatch.setattr(lv, "verify_files", fake_verify)
    monkeypatch.setattr(lv, "self_heal",   hanging_self_heal)

    async def go():
        start = time.time()
        await eng._do_verify()
        return time.time() - start

    elapsed = asyncio.run(go())
    # Must complete within a few seconds (timeout × MAX_SELF_HEALS + budget).
    assert elapsed < 10, f"verify hung for {elapsed:.1f}s — timeout broken"
    # Phase ended in PAUSED_FOR_USER (heals exhausted with timeouts).
    assert eng.state == le.LoopState.PAUSED_FOR_USER


# ──────────────────────────────────────────────────────────────────
# Bug #5 — verify_files runs lints concurrently
# ──────────────────────────────────────────────────────────────────
def test_verify_files_uses_semaphore_parallelism():
    """Concurrent linter calls — implementation contract test that
    pins the semaphore presence so a refactor doesn't silently
    revert to the serial for-loop."""
    src = open("/app/backend/services/loop_verify.py").read()
    assert "asyncio.Semaphore" in src
    assert "asyncio.gather" in src


# ──────────────────────────────────────────────────────────────────
# Bug #6 — Execute on empty plan must _fail, not silently progress
# ──────────────────────────────────────────────────────────────────
def test_execute_empty_files_completes_as_readonly_report(monkeypatch):
    """Iter 331 — design change (founder-reported prod loop 6de15d4c).

    Old behaviour (iter212m-131 bug #6): empty files_to_change →
    _fail("execute"). That punished read-only/query loops ("list my
    repo files", "explain X") — the most common ask. New behaviour:
    the loop terminates COMPLETED as a read-only report, _fail is
    NEVER called, and the terminal state stops the pipeline so the
    original bug #6 concern (fake "Ship complete") still can't happen.
    """
    from services import loop_engine as le

    eng = _make_engine()
    eng.context["plan"] = {
        "title": "t", "bullets": ["b1"], "files_to_change": [],
    }
    fail_calls = []
    orig_fail = eng._fail
    async def spy_fail(phase, message):
        fail_calls.append((phase, message))
        await orig_fail(phase, message)
    monkeypatch.setattr(eng, "_fail", spy_fail)

    async def go():
        await eng._do_execute()

    asyncio.run(go())
    assert fail_calls == [], "read-only plan must not fail the loop"
    assert eng.state == le.LoopState.COMPLETED
    assert eng._should_stop(), "terminal state must halt the pipeline"


# ──────────────────────────────────────────────────────────────────
# Bug #7 — phase restart clears phase-specific context
# ──────────────────────────────────────────────────────────────────
def test_with_budget_restart_clears_phase_context(monkeypatch):
    """When a phase times out and auto-restarts, its phase-specific
    context keys must be cleared so the next attempt isn't confused
    by stale partial data."""
    from services import loop_engine as le

    eng = _make_engine()
    # Pre-populate stale partial data from a "first run" that timed out.
    eng.context["submitted_files"]      = [{"path": "stale.py", "content": "x"}]
    eng.context["files_changed"]        = ["stale.py"]
    eng.context["verification_results"] = {"ok": False, "stale": True}

    call_count = {"n": 0}
    async def fast_then_done():
        # First call times out; second succeeds.
        call_count["n"] += 1
        if call_count["n"] == 1:
            await asyncio.sleep(10)   # > budget
        # Successful second call would clean context itself in real code.

    # Shrink the execute budget so the test runs in <1s.
    monkeypatch.setitem(le.PHASE_TIMEOUTS_S, "execute", 1)

    async def go():
        await eng._with_budget("execute", fast_then_done)

    asyncio.run(go())
    # After restart, the stale phase-specific keys must have been
    # cleared (Bug #7 fix).  files_changed + submitted_files reset
    # to empty list; the verification_results key untouched (that's
    # the VERIFY phase's responsibility, not execute).
    assert eng.context["submitted_files"]   == []
    assert eng.context["files_changed"]     == []


# ──────────────────────────────────────────────────────────────────
# Bug #9 — submit_files refuses mid-Execute writes
# ──────────────────────────────────────────────────────────────────
def test_submit_files_rejects_mid_execute(monkeypatch):
    from services import loop_engine as le
    eng = _make_engine()
    eng.state = le.LoopState.EXECUTING
    with pytest.raises(ValueError, match="refused"):
        asyncio.run(eng.submit_files([{"path": "x.py", "content": "y=1"}]))


def test_submit_files_allowed_pre_confirm():
    from services import loop_engine as le
    eng = _make_engine()
    eng.state = le.LoopState.AWAITING_CONFIRMATION
    asyncio.run(eng.submit_files([{"path": "x.py", "content": "y = 1"}]))
    assert eng.context["submitted_files"] == [{"path": "x.py", "content": "y = 1"}]


# ──────────────────────────────────────────────────────────────────
# Bug #10 — restart sets state BEFORE emitting SELF_HEALING
# ──────────────────────────────────────────────────────────────────
def test_with_budget_state_before_emit_on_restart(monkeypatch):
    """Contract test: state change must precede the SELF_HEALING
    emit so SSE consumers never observe a self_healing event
    while state is still the previous phase's running state."""
    src = open("/app/backend/services/loop_engine.py").read()
    # Pattern: the SELF_HEALING assignment must appear BEFORE the
    # emit(SELF_HEALING, ...) call in _with_budget.
    idx_state = src.find("self.state = LoopState.SELF_HEALING")
    idx_emit  = src.find("LoopState.SELF_HEALING, \"self_heal\"")
    assert idx_state > 0 and idx_emit > 0
    assert idx_state < idx_emit, "state mutation must precede emit"
