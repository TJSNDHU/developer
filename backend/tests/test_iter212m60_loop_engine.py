"""
test_iter212m60_loop_engine.py — Phase B regression for the
LoopEngine state machine.

Mocks Mongo with a tiny in-memory async double + monkeypatches
`_generate_plan` so the LLM is never actually called.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import loop_engine as eng                  # noqa: E402


# ─── Tiny in-memory Mongo double ──────────────────────────────────────

class _Coll:
    def __init__(self):
        self.docs: list[dict] = []

    async def update_one(self, filter, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filter.items()):
                d.update(update.get("$set", {}))
                d.update(update.get("$setOnInsert", {}))
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            new = {**filter}
            new.update(update.get("$set", {}))
            new.update(update.get("$setOnInsert", {}))
            self.docs.append(new)
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": "x"})()

    async def find_one(self, filter, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filter.items()):
                out = dict(d)
                out.pop("_id", None)
                return out
        return None

    def find(self, filter):
        # crude predicate for the resume_stale test: support $in and $lt
        docs = list(self.docs)
        if "state" in filter and isinstance(filter["state"], dict):
            allowed = filter["state"].get("$in", [])
            docs = [d for d in docs if d.get("state") in allowed]
        if "updated_at" in filter and isinstance(filter["updated_at"], dict):
            cutoff = filter["updated_at"].get("$lt")
            docs = [d for d in docs if d.get("updated_at") and d["updated_at"] < cutoff]

        async def _gen():
            for d in docs:
                yield d
        return _gen()


class _DB:
    def __init__(self):
        self.loop_sessions = _Coll()
        self.loop_plans    = _Coll()
        self.loop_errors   = _Coll()
        self.loop_backups  = _Coll()


# ─── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_registry():
    eng.reset_registry()
    yield
    eng.reset_registry()


@pytest.fixture
def fake_plan(monkeypatch):
    async def _plan(uid, pid, msg):
        return {
            "title": "Test plan",
            "files_to_change": ["a.py", "b.py"],
            "bullets": ["one", "two", "three"],
            "estimated_time": "~1 min",
        }
    monkeypatch.setattr(eng, "_generate_plan", _plan)


@pytest.fixture
def fast_timeouts(monkeypatch):
    # Speed the tests up by 100x.
    monkeypatch.setitem(eng.PHASE_TIMEOUTS_S, "plan",    2)
    monkeypatch.setitem(eng.PHASE_TIMEOUTS_S, "execute", 2)
    monkeypatch.setitem(eng.PHASE_TIMEOUTS_S, "verify",  2)
    monkeypatch.setitem(eng.PHASE_TIMEOUTS_S, "scan",    2)
    monkeypatch.setitem(eng.PHASE_TIMEOUTS_S, "ship",    2)


# ─── Tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_plan_phase_emits_awaiting_confirmation(fake_plan, fast_timeouts):
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", "p1", "build login")
    events = [e async for e in engine.start()]
    assert events, "engine should emit at least one event"
    last = events[-1]
    assert last["state"] == "awaiting_confirmation"
    assert last["requires_user_action"] is True
    assert last["data"]["plan"]["title"] == "Test plan"
    assert engine.state == eng.LoopState.AWAITING_CONFIRMATION


@pytest.mark.asyncio
async def test_confirm_yes_runs_pipeline_to_completion(fake_plan, fast_timeouts):
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", "p1", "build login")
    [_ async for _ in engine.start()]
    await engine.confirm(True, "")
    # Pipeline runs in a background task — give it a tick.
    for _ in range(30):
        if engine.state in eng._TERMINAL:
            break
        await asyncio.sleep(0.05)
    assert engine.state == eng.LoopState.COMPLETED
    assert engine.context["commit"]["message"].startswith("feat(ora):")


@pytest.mark.asyncio
async def test_confirm_no_aborts(fake_plan, fast_timeouts):
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", "p1", "x")
    [_ async for _ in engine.start()]
    await engine.confirm(False, "too risky")
    assert engine.state == eng.LoopState.ABORTED


@pytest.mark.asyncio
async def test_phase_timeout_marks_failed(monkeypatch, fake_plan):
    """If plan generation never returns, plan-phase budget elapses."""
    async def _hang(*_a, **_k):
        await asyncio.sleep(10)
    monkeypatch.setattr(eng, "_generate_plan", _hang)
    monkeypatch.setitem(eng.PHASE_TIMEOUTS_S, "plan", 1)
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", "p1", "x")
    [_ async for _ in engine.start()]
    assert engine.state == eng.LoopState.FAILED
    assert "budget" in engine.context["errors_encountered"][-1]["error"].lower()


@pytest.mark.asyncio
async def test_resume_stale_flips_to_paused(fake_plan):
    db = _DB()
    from datetime import datetime, timezone
    # Seed a session that looks orphaned (3 minutes stale).
    stale_ts = datetime.now(timezone.utc) - timedelta(minutes=3)
    db.loop_sessions.docs.append({
        "loop_id":    "loop_stale",
        "user_id":    "u1",
        "state":      eng.LoopState.EXECUTING.value,
        "phase":      "execute",
        "updated_at": stale_ts,
    })
    rescued = await eng.resume_stale(db)
    assert rescued == 1
    doc = db.loop_sessions.docs[0]
    assert doc["state"] == eng.LoopState.PAUSED_FOR_USER.value
    assert doc["resume_reason"] == "server_restart_mid_loop"


@pytest.mark.asyncio
async def test_cancel_marks_aborted(fake_plan, fast_timeouts):
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", "p1", "x")
    [_ async for _ in engine.start()]
    await engine.cancel()
    assert engine.state == eng.LoopState.ABORTED


@pytest.mark.asyncio
async def test_registry_register_lookup_deregister(fake_plan, fast_timeouts):
    db = _DB()
    engine = eng.LoopEngine(db, "loop_xyz", "u1", "p1", "x")
    eng.register(engine)
    assert eng.lookup("loop_xyz") is engine
    eng.deregister("loop_xyz")
    assert eng.lookup("loop_xyz") is None


@pytest.mark.asyncio
async def test_backup_and_rollback_capture_files(fake_plan):
    db = _DB()
    await eng.record_backup(db, "loop_a", "src/foo.py", "old content")
    await eng.record_backup(db, "loop_a", "src/bar.py", "second file")
    items = await eng.rollback(db, "loop_a")
    assert len(items) == 2
    paths = {i["path"] for i in items}
    assert paths == {"src/foo.py", "src/bar.py"}


@pytest.mark.asyncio
async def test_event_schema_complete(fake_plan, fast_timeouts):
    """Every emitted event must include every key from the spec."""
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", "p1", "x")
    events = [e async for e in engine.start()]
    required = {"loop_id", "state", "phase", "step", "total_steps",
                "message", "data", "timestamp", "requires_user_action"}
    for ev in events:
        assert required.issubset(ev.keys()), f"missing keys: {required - set(ev.keys())}"


@pytest.mark.asyncio
async def test_error_logged_on_failure(monkeypatch, fake_plan):
    async def _boom(*_a, **_k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(eng, "_generate_plan", _boom)
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", "p1", "x")
    [_ async for _ in engine.start()]
    assert engine.state == eng.LoopState.FAILED
    assert len(db.loop_errors.docs) >= 1
    assert "kaboom" in db.loop_errors.docs[-1]["error"]


@pytest.mark.asyncio
async def test_commit_message_carries_loop_verified_tag(fake_plan, fast_timeouts):
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", "p1", "Add OAuth flow")
    [_ async for _ in engine.start()]
    await engine.confirm(True, "")
    for _ in range(30):
        if engine.state in eng._TERMINAL: break
        await asyncio.sleep(0.05)
    assert engine.state == eng.LoopState.COMPLETED
    msg = engine.context["commit"]["message"]
    assert msg.startswith("feat(ora):")
    assert "[loop-verified]" in msg


@pytest.mark.asyncio
async def test_no_silent_failure_in_scan(monkeypatch, fake_plan, fast_timeouts):
    """If the security-scan helper throws, the loop must log & continue."""
    async def _scan_boom(*_a, **_k):
        raise ValueError("scanner busted")
    monkeypatch.setattr(eng, "_run_security_scan", _scan_boom)
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", "p1", "x")
    [_ async for _ in engine.start()]
    await engine.confirm(True, "")
    for _ in range(40):
        if engine.state in eng._TERMINAL: break
        await asyncio.sleep(0.05)
    # Engine should still finish (G1 — scan failure logged, not silent).
    assert engine.state == eng.LoopState.COMPLETED
    assert any("scanner busted" in d["error"]
               for d in db.loop_errors.docs)
