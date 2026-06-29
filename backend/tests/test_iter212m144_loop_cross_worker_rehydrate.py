"""
Iter 212m-144 — Cross-worker Loop engine rehydration.

LIVE PROD REPRO (Feb 2026 founder QA on `auremcto.com`):
  • POST /loop/start → 200, returned loop_id, state=awaiting_confirmation.
  • Plan content was correctly persisted to Mongo `loop_sessions`.
  • POST /loop/{id}/confirm → 404 "Loop not found or already finished"
    immediately after the start (~80ms later).

ROOT CAUSE:
  `_LIVE` is a per-process in-memory dict. With multiple uvicorn
  workers in PROD, `start()` may create the engine in worker A's
  `_LIVE` while `confirm()` lands on worker B — worker B's `lookup()`
  returns None and the router 404s. The Mongo session was correctly
  persisted by worker A, but worker B never consulted it.

FIX:
  New `lookup_or_rehydrate(db, loop_id)` helper. Local lookup first;
  if absent, load the persisted session doc from Mongo and rebuild a
  fresh LoopEngine instance with the same state + context, register
  it in this worker's `_LIVE`, and return it. Safety guard: only
  rehydrate when the persisted state is PAUSED (AWAITING_CONFIRMATION
  or PAUSED_FOR_USER) so we never split-brain a running pipeline.

  All confirm / confirm-ship / pause-response / submit-files / cancel
  endpoints in `routers/loop.py` now route through the rehydrating
  helper. Stream is intentionally still local-only because the queue
  is in-memory and can't be reconstructed from Mongo.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import loop_engine as eng
from services.loop_engine import LoopEngine, LoopState


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_registry():
    eng.reset_registry()
    yield
    eng.reset_registry()


class _FakeDB:
    def __init__(self, sessions: list[dict]):
        self._sessions = list(sessions)
        self.loop_sessions = SimpleNamespace(
            find_one=self._find_one,
        )

    async def _find_one(self, filt, projection=None):
        for d in self._sessions:
            if all(d.get(k) == v for k, v in filt.items()):
                return {k: v for k, v in d.items() if k != "_id"}
        return None


def _persisted_doc(state="awaiting_confirmation", phase="plan"):
    return {
        "loop_id": "loop_test_abc",
        "user_id": "u1",
        "project_id": "p1",
        "state": state,
        "phase": phase,
        "context": {
            "original_request": "do the thing",
            "plan": {
                "title": "Plan",
                "files_to_change": ["a.py"],
                "bullets": ["1. do"],
            },
            "files_changed": [],
            "errors_encountered": [],
            "self_heals_performed": [],
            "verification_results": {},
            "scan_results": {},
            "commit": None,
        },
    }


async def test_lookup_returns_local_engine_when_in_live():
    """Local-first fast path — no Mongo call when engine exists."""
    engine = LoopEngine(
        db=None, loop_id="loop_local", user_id="u1",
        project_id="p1", user_message="hi",
    )
    eng.register(engine)
    result = await eng.lookup_or_rehydrate(_FakeDB([]), "loop_local")
    assert result is engine


async def test_rehydrate_from_mongo_when_local_miss():
    """The exact PROD repro: local _LIVE is empty (different worker),
    but Mongo has the session doc. Rehydrate, register, return."""
    db = _FakeDB([_persisted_doc(state="awaiting_confirmation")])
    result = await eng.lookup_or_rehydrate(db, "loop_test_abc")
    assert result is not None
    assert result.loop_id == "loop_test_abc"
    assert result.user_id == "u1"
    assert result.project_id == "p1"
    assert result.state == LoopState.AWAITING_CONFIRMATION
    assert result.phase == "plan"
    # Context must be preserved.
    assert result.context["plan"]["title"] == "Plan"
    # Engine must be registered in _LIVE on this worker now.
    assert eng.lookup("loop_test_abc") is result


async def test_rehydrate_returns_none_when_no_persisted_session():
    """Empty Mongo + empty _LIVE → None → caller 404s cleanly."""
    db = _FakeDB([])
    result = await eng.lookup_or_rehydrate(db, "loop_missing")
    assert result is None


async def test_rehydrate_refuses_running_loops():
    """Safety: never rehydrate an engine that's mid-execution on
    another worker — that would create a split-brain (two engines
    racing on the same loop_id). Only PAUSED loops are safe."""
    db = _FakeDB([_persisted_doc(state="executing")])
    result = await eng.lookup_or_rehydrate(db, "loop_test_abc")
    assert result is None, (
        "Must refuse rehydration for non-paused states to avoid "
        "split-brain across workers."
    )


async def test_rehydrate_allows_paused_for_user_state():
    """Loops paused at the Ship gate (PAUSED_FOR_USER) must also be
    rehydratable so cross-worker confirm-ship works."""
    db = _FakeDB([_persisted_doc(state="paused_for_user", phase="ship")])
    result = await eng.lookup_or_rehydrate(db, "loop_test_abc")
    assert result is not None
    assert result.state == LoopState.PAUSED_FOR_USER
    assert result.phase == "ship"


async def test_rehydrate_handles_none_db_gracefully():
    """If get_db() returns None (DB outage), don't crash."""
    result = await eng.lookup_or_rehydrate(None, "anything")
    assert result is None


async def test_rehydrate_handles_unknown_state_string():
    """Defensive: an unrecognised state string in Mongo (e.g. from
    a future enum value) should not crash — fall back safely."""
    doc = _persisted_doc(state="awaiting_confirmation")
    doc["state"] = "totally_made_up_state"
    db = _FakeDB([doc])
    result = await eng.lookup_or_rehydrate(db, "loop_test_abc")
    # Unknown state isn't in the paused-allowlist, so refuse.
    assert result is None


# ── Router-level contract ─────────────────────────────────────────────
def test_loop_router_uses_rehydrate_on_confirm():
    """Source-pattern contract: `routers/loop.py` `confirm` /
    `confirm-ship` / `pause-response` / `submit-files` / `cancel`
    must all call `eng.lookup_or_rehydrate(...)` (not `eng.lookup(...)`
    only) so cross-worker flows survive."""
    from pathlib import Path
    src = Path("/app/backend/routers/loop.py").read_text(encoding="utf-8")
    # Each rewritten endpoint must reference the rehydrating helper.
    assert src.count("eng.lookup_or_rehydrate(") >= 4, (
        "Expected at least 4 endpoints to use lookup_or_rehydrate "
        "(confirm, confirm-ship, pause-response, submit-files, cancel)."
    )
    # The Iter 212m-144 marker must be present so future agents
    # understand why these endpoints have the extra Mongo round-trip.
    assert "Iter 212m-144" in src
