"""
test_iter212m62_loop_verify.py — Phase C regression for the real
ruff + eslint verifier and the self-heal loop in LoopEngine.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import loop_verify, loop_engine as eng    # noqa: E402


# ─── Static verifier ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_clean_python_passes():
    rep = await loop_verify.verify_files([
        {"path": "ok.py", "content": "def add(a, b):\n    return a + b\n"},
    ])
    assert rep["ok"] is True
    assert rep["results"][0]["linter"] == "ruff"
    assert rep["results"][0]["ok"] is True


@pytest.mark.asyncio
async def test_verify_broken_python_fails():
    rep = await loop_verify.verify_files([
        {"path": "bad.py",
         "content": "def add(a, b)\n    return a + b\n"},   # missing :
    ])
    assert rep["ok"] is False
    assert any("bad.py" in e for e in rep["errors"])


@pytest.mark.asyncio
async def test_verify_skips_unknown_extension():
    rep = await loop_verify.verify_files([
        {"path": "readme.md", "content": "# hi\n"},
    ])
    assert rep["ok"] is True
    assert rep["results"][0]["linter"] == "skip"


@pytest.mark.asyncio
async def test_verify_eslint_picks_up_undef():
    rep = await loop_verify.verify_files([
        {"path": "bad.js", "content": "function f() { undeclared_var = 1; }\n"},
    ])
    # eslint with no-undef:error should flag this.
    assert rep["ok"] is False
    assert rep["results"][0]["linter"] == "eslint"


@pytest.mark.asyncio
async def test_verify_empty_input_returns_ok():
    rep = await loop_verify.verify_files([])
    assert rep["ok"] is True
    assert rep["results"] == []


# ─── Engine integration: self-heal flow ───────────────────────────────

class _Coll:
    def __init__(self): self.docs = []
    async def update_one(self, f, u, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in f.items()):
                d.update(u.get("$set", {}))
                d.update(u.get("$setOnInsert", {}))
                return type("R", (), {"matched_count": 1})()
        if upsert:
            new = {**f}
            new.update(u.get("$set", {}))
            new.update(u.get("$setOnInsert", {}))
            self.docs.append(new)
        return type("R", (), {"matched_count": 0})()
    async def insert_one(self, doc): self.docs.append(dict(doc))
    async def find_one(self, f, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in f.items()):
                return dict(d)
        return None
    def find(self, f):
        async def _g():
            for d in self.docs: yield d
        return _g()


class _DB:
    def __init__(self):
        for col in ("loop_sessions", "loop_plans",
                    "loop_errors", "loop_backups"):
            setattr(self, col, _Coll())


@pytest.fixture(autouse=True)
def _reset(): eng.reset_registry(); yield; eng.reset_registry()


@pytest.fixture
def fake_plan(monkeypatch):
    # Iter 309 · Phase 0.3 — files_to_change was empty here, which
    # made every test using this fixture short-circuit at execute
    # with "plan has no files_to_change, failing" → engine state
    # became FAILED at execute, and NEVER reached the verify/self-heal
    # path some tests were actually asserting about. Now the fake
    # plan carries a placeholder file so the pipeline actually
    # traverses execute → verify → self-heal and the downstream
    # assertions test what they were designed to.
    async def _plan(uid, pid, msg):
        return {"title": "t", "files_to_change": ["bad.py"],
                "bullets": ["x"], "estimated_time": "?"}
    monkeypatch.setattr(eng, "_generate_plan", _plan)


@pytest.fixture
def fast_timeouts(monkeypatch):
    for k in ("plan", "execute", "verify", "scan", "ship", "self_heal"):
        monkeypatch.setitem(eng.PHASE_TIMEOUTS_S, k, 30)


# Iter 309-c — After Phase 0's Execute rewrite (GitHub-creds gate at
# line ~991 of loop_engine.py, Parliament LLM per file), `_do_execute`
# refuses to run without a real BINContext or a cto_projects doc with
# GitHub OAuth/PAT.  These m62 tests target the VERIFY + SELF-HEAL
# path specifically — the caller has already `submit_files()`'d the
# code they want linted, so Execute should be a no-op that forwards
# those files to Verify.  We stub `_do_execute` with a minimal
# forwarder AND stub `_do_ship` (no real GitHub commit) so the
# COMPLETED-state assertions actually pass without a repo.  Verify
# (`_do_verify`) + Scan (`_run_security_scan`, patched per-test) run
# natively, which is exactly what these tests are designed to cover.
@pytest.fixture
def stub_execute_and_ship(monkeypatch):
    async def _stub_execute(self):
        self.state = eng.LoopState.EXECUTING
        self.phase = "execute"
        # If the caller pre-populated submitted_files via
        # submit_files(), keep it verbatim so `_do_verify` sees
        # exactly the code the test intended.  Otherwise seed
        # from the plan for the "no submitted files" case.
        existing = self.context.get("submitted_files")
        if not existing:
            plan_files = (self.context.get("plan") or {}).get("files_to_change") or []
            # test_verify_skipped_when_no_files_submitted asserts the
            # `skipped_no_files` branch — leave submitted_files empty
            # for that case so `_do_verify` short-circuits.
            self.context["submitted_files"] = []
        await self._emit(eng.LoopState.EXECUTING, "execute",
                         step=2, total_steps=5,
                         message="Executing (stub)…")

    async def _stub_ship(self):
        self.state = eng.LoopState.SHIPPING
        self.phase = "ship"
        self.context["commit"] = {
            "message": f"feat(ora): {self.user_message} [loop-verified]",
            "sha":     "deadbeefcafebabe",
        }
        await self._emit(eng.LoopState.SHIPPING, "ship",
                         step=5, total_steps=5, message="Shipping (stub)…")
        self.state = eng.LoopState.COMPLETED
        await self._emit(eng.LoopState.COMPLETED, "ship",
                         step=5, total_steps=5,
                         message="Loop complete (stub).")

    monkeypatch.setattr(eng.LoopEngine, "_do_execute", _stub_execute)
    monkeypatch.setattr(eng.LoopEngine, "_do_ship",    _stub_ship)


@pytest.mark.asyncio
async def test_self_heal_fixes_broken_python(monkeypatch, fake_plan, fast_timeouts, stub_execute_and_ship):
    """If the verifier fails, self-heal rewrites and pass on retry."""
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", None, "fix it")
    [_ async for _ in engine.start()]
    await engine.submit_files([
        {"path": "bad.py", "content": "def add(a, b)\n    return a + b\n"},
    ])
    # Iter 309-c — verify phase now uses Parliament.healer.heal
    # (iter 212m-150 refactor) instead of loop_verify.self_heal.
    # Mock the parliament path so the test still exercises the
    # heal-round → re-verify → COMPLETED flow it was designed for.
    from core import parliament as parl_mod
    async def _heal(self, *, task, all_attempts, round_num, max_rounds):
        return {"status": "retry",
                "output": "def add(a, b):\n    return a + b\n"}
    monkeypatch.setattr(parl_mod.SelfHeal, "heal", _heal)
    # Skip scan (Phase C calls real GitHub — bypass with stub).
    async def _scan(*a, **kw):
        return {"summary": {"total": 0, "by_severity": {}}}
    monkeypatch.setattr(eng, "_run_security_scan", _scan)
    await engine.confirm(True, "")
    for _ in range(50):
        if engine.state in eng._TERMINAL: break
        await asyncio.sleep(0.1)
    assert engine.state == eng.LoopState.COMPLETED, \
        f"expected COMPLETED, got {engine.state}"
    vr = engine.context["verification_results"]
    assert vr["ok"] is True
    heals = engine.context["self_heals_performed"]
    assert len(heals) >= 1
    assert any(h.get("applied") for h in heals)


@pytest.mark.asyncio
async def test_self_heal_exhausted_pauses_for_user(
        monkeypatch, fake_plan, fast_timeouts, stub_execute_and_ship):
    """If self-heal can't fix it after MAX attempts, loop pauses."""
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", None, "fix it")
    [_ async for _ in engine.start()]
    await engine.submit_files([
        {"path": "bad.py", "content": "def f(\n  pass\n"},
    ])
    # Iter 309-c — Parliament heal path returns the SAME broken
    # content every round, exhausting MAX_SELF_HEALS.  Kept as a
    # "retry" status so the healer code path stays live (an
    # "escalate" would short-circuit into a different branch).
    from core import parliament as parl_mod
    async def _heal(self, *, task, all_attempts, round_num, max_rounds):
        return {"status": "retry", "output": "def f(\n  pass\n"}
    monkeypatch.setattr(parl_mod.SelfHeal, "heal", _heal)
    await engine.confirm(True, "")
    for _ in range(50):
        if engine.state in {eng.LoopState.PAUSED_FOR_USER,
                            eng.LoopState.COMPLETED,
                            eng.LoopState.FAILED}:
            break
        await asyncio.sleep(0.1)
    assert engine.state == eng.LoopState.PAUSED_FOR_USER


@pytest.mark.asyncio
async def test_verify_skipped_when_no_files_submitted(
        monkeypatch, fake_plan, fast_timeouts, stub_execute_and_ship):
    """No submitted files → verify passes through with the flag set."""
    async def _scan(*a, **kw):
        return {"summary": {"total": 0, "by_severity": {}}}
    monkeypatch.setattr(eng, "_run_security_scan", _scan)
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", None, "x")
    [_ async for _ in engine.start()]
    await engine.confirm(True, "")
    for _ in range(50):
        if engine.state in eng._TERMINAL: break
        await asyncio.sleep(0.1)
    assert engine.state == eng.LoopState.COMPLETED
    assert engine.context["verification_results"].get("skipped_no_files") is True
