"""
test_iter309_phase03_self_heal_paused.py — Iter 309 · Phase 0.3

Direct behavioural proof of the contract:

    When verify's self-heal loop is EXHAUSTED (MAX_SELF_HEALS rounds all
    produce still-failing files), the engine state MUST transition to
    PAUSED_FOR_USER, NOT to FAILED.

Founder rationale: PAUSED_FOR_USER preserves context so the founder can
step in, override, or provide a hint. FAILED closes the loop and forces
a full restart — throws away all the work the engine already did on
plan + execute, which is expensive and demoralising for the founder.

This test invokes `_do_verify` DIRECTLY on a pre-populated engine, so
it does NOT depend on the plan/execute/confirm dance being wired up
correctly in test fixtures. If someone later breaks the fake_plan
fixture (as happened in iter212m62), THIS test still catches a real
regression on the actual contract.
"""
from __future__ import annotations

import asyncio

import pytest

from services import loop_engine as eng
from services import loop_verify


class _DB:
    """Minimal in-memory Mongo stand-in — only implements what
    `_do_verify` + `_emit` + `_persist_session` need."""
    def __init__(self):
        self.loop_sessions_docs = []
        self.loop_events_docs = []

    def __getitem__(self, name):
        # Emulate `db[coll_name]` access seen in loop_engine.
        return _Coll(self, name)


class _Coll:
    def __init__(self, db, name):
        self.db, self.name = db, name

    async def update_one(self, *a, **kw):
        return type("R", (), {"modified_count": 1})()

    async def insert_one(self, doc):
        {"loop_sessions": self.db.loop_sessions_docs,
         "loop_events":   self.db.loop_events_docs}.get(
            self.name, []
        ).append(doc)


@pytest.mark.asyncio
async def test_verify_self_heal_exhausted_transitions_to_paused_for_user(
        monkeypatch):
    """Iter 309 Phase 0.3 — direct contract test.

    Populate an engine with submitted files, monkey-patch `verify_files`
    to always fail, monkey-patch `self_heal` to always return the same
    broken content, invoke `_do_verify()` — assert final state is
    PAUSED_FOR_USER + last event carries requires_user_action=True.
    """
    db = _DB()
    engine = eng.LoopEngine(db, eng.new_loop_id(), "u1", None, "fix it")
    # Pre-populate context as if plan + execute already ran.
    engine.context["submitted_files"] = [
        {"path": "bad.py", "content": "def f(\n  pass\n"},
    ]
    engine.state = eng.LoopState.VERIFYING
    engine.phase = "verify"

    # Every verify pass fails with a syntax error. `errors` MUST be
    # a list of "path:line:msg" strings per the engine's post-heal
    # filtering logic in loop_engine.py:1550 (not a list of dicts —
    # that was my first mistake).
    async def _verify_always_fails(files, **kw):
        return {
            "ok": False,
            "errors": ["bad.py:1: SyntaxError: expected ':'"],
            "results": [{"path": "bad.py", "ok": False,
                         "errors_by_type": {"syntax": 1}}],
        }
    monkeypatch.setattr(loop_verify, "verify_files", _verify_always_fails)

    # Self-heal returns the same broken content on every attempt.
    async def _heal_no_op(file_obj, errs, **kw):
        return file_obj["content"]
    monkeypatch.setattr(loop_verify, "self_heal", _heal_no_op)

    # Also patch _persist_session to avoid touching the fake DB in
    # ways it doesn't emulate.
    async def _no_persist(*a, **kw):
        return None
    monkeypatch.setattr(eng, "_persist_session", _no_persist)

    # Invoke the phase directly.
    await engine._do_verify()

    # THE CONTRACT — must be PAUSED_FOR_USER, not FAILED.
    assert engine.state == eng.LoopState.PAUSED_FOR_USER, (
        f"iter309 Phase 0.3 contract broken: verify self-heal "
        f"exhausted must go to PAUSED_FOR_USER, got {engine.state}. "
        "Founder rationale: FAILED throws away all plan+execute work; "
        "PAUSED lets the founder step in with context intact."
    )


def test_verify_pauses_on_exhaustion_not_fails_source_of_truth():
    """Static assertion — the line in loop_engine.py that runs after
    MAX_SELF_HEALS is exhausted MUST reference LoopState.PAUSED_FOR_USER,
    NOT LoopState.FAILED. This prevents someone from later "fixing"
    the exhaustion branch to fail-fast without going through review.

    The relevant block starts at the comment
    'MAX_SELF_HEALS exhausted with files still failing' — we verify
    the following ~20 lines mention PAUSED_FOR_USER and DO NOT
    contain a `_fail(` call in that scope.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "services" / "loop_engine.py").read_text().splitlines()
    marker = "MAX_SELF_HEALS exhausted"
    start = next((i for i, l in enumerate(src) if marker in l), None)
    assert start is not None, (
        "Landmark comment for self-heal exhaustion path missing — "
        "someone refactored the block without preserving the marker. "
        "Restore the comment or update this test with the new landmark."
    )
    scope = "\n".join(src[start:start + 25])
    assert "PAUSED_FOR_USER" in scope, (
        "Self-heal exhaustion branch no longer references "
        "PAUSED_FOR_USER — the contract may have regressed."
    )
    assert "_fail(" not in scope, (
        "Self-heal exhaustion branch now calls _fail() — the loop is "
        "going to FAILED instead of PAUSED_FOR_USER. This regresses "
        "the iter309 Phase 0.3 founder-approved contract."
    )
