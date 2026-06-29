"""
Iter 212m-117 — Tests for:
  1. Auto graph refresh before _do_plan when graph >30 min old
  2. GET /loop/active resume on browser refresh (already in iter 115 tests)
  3. Trust-level toggle (L1/L2/L3) — endpoints + Loop wiring
"""
from __future__ import annotations

import pytest


# ─── 1. Auto graph refresh ───────────────────────────────────────────
def test_generate_plan_has_auto_graph_refresh_logic():
    src = open("/app/backend/services/loop_engine.py").read()
    plan_block = src.split("async def _generate_plan(", 1)[1].split("async def _save_plan(", 1)[0] if "async def _save_plan(" in src else src.split("async def _generate_plan(", 1)[1]
    # Must check graph age and rebuild if stale.
    assert "30 * 60" in plan_block, "30-min staleness threshold must be present"
    assert "build_graph" in plan_block
    assert "built_at" in plan_block
    # Must be best-effort — exceptions are swallowed.
    assert "silent graph refresh skipped" in plan_block or "graph refresh skipped" in plan_block


# ─── 2. GET /loop/active wired on frontend ────────────────────────────
def test_chatpanel_hydrates_paused_ship_on_mount():
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()
    assert "/loop/active" in src
    assert "ship_pending" in src
    # Sets shipPending on state=paused_for_user, phase=ship.
    assert 'state === "paused_for_user"' in src
    assert "setShipPending" in src


# ─── 3. Trust-level (L1/L2/L3) ────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_trust_level_returns_default_when_unset(monkeypatch):
    from routers import trust_level as tl
    async def fake_current_dev(auth=None): return {"user_id": "u1"}
    monkeypatch.setattr(tl, "current_dev", fake_current_dev)

    class _Users:
        async def find_one(self, q, proj=None): return None
    class _DB: dev_users = _Users()
    monkeypatch.setattr(tl, "get_db", lambda: _DB())

    res = await tl.get_trust_level(authorization="Bearer x")
    assert res["ok"] is True
    assert res["trust_level"] == "L2"
    assert res["default"] == "L2"


@pytest.mark.asyncio
async def test_get_trust_level_returns_stored_value(monkeypatch):
    from routers import trust_level as tl
    async def fake_current_dev(auth=None): return {"user_id": "u1"}
    monkeypatch.setattr(tl, "current_dev", fake_current_dev)

    class _Users:
        async def find_one(self, q, proj=None):
            return {"trust_level": "L3"}
    class _DB: dev_users = _Users()
    monkeypatch.setattr(tl, "get_db", lambda: _DB())

    res = await tl.get_trust_level(authorization="Bearer x")
    assert res["trust_level"] == "L3"


@pytest.mark.asyncio
async def test_set_trust_level_persists_to_db(monkeypatch):
    from routers import trust_level as tl
    async def fake_current_dev(auth=None): return {"user_id": "u1"}
    monkeypatch.setattr(tl, "current_dev", fake_current_dev)

    updates: list[dict] = []
    class _Users:
        async def update_one(self, q, u):
            updates.append({"q": q, "u": u})
            return type("R", (), {"modified_count": 1})()
    class _DB: dev_users = _Users()
    monkeypatch.setattr(tl, "get_db", lambda: _DB())

    body = tl.TrustLevelBody(trust_level="L3")
    res = await tl.set_trust_level(body=body, authorization="Bearer x")
    assert res["ok"] is True
    assert res["trust_level"] == "L3"
    assert updates[0]["u"] == {"$set": {"trust_level": "L3"}}


@pytest.mark.asyncio
async def test_set_trust_level_rejects_invalid_value():
    from routers import trust_level as tl
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        tl.TrustLevelBody(trust_level="L5")
    with pytest.raises(pydantic.ValidationError):
        tl.TrustLevelBody(trust_level="admin")


@pytest.mark.asyncio
async def test_get_user_trust_level_helper_defaults_to_l2():
    from routers.trust_level import get_user_trust_level
    # No DB.
    assert await get_user_trust_level(None, "u1") == "L2"


@pytest.mark.asyncio
async def test_get_user_trust_level_helper_returns_stored():
    from routers.trust_level import get_user_trust_level
    class _Users:
        async def find_one(self, q, proj=None):
            return {"trust_level": "L1"}
    class _DB: dev_users = _Users()
    assert await get_user_trust_level(_DB(), "u1") == "L1"


@pytest.mark.asyncio
async def test_get_user_trust_level_falls_back_on_invalid_stored():
    """Defensive — if someone hand-edits the DB to an invalid value,
    we return the safe default."""
    from routers.trust_level import get_user_trust_level
    class _Users:
        async def find_one(self, q, proj=None):
            return {"trust_level": "bogus"}
    class _DB: dev_users = _Users()
    assert await get_user_trust_level(_DB(), "u1") == "L2"


# ─── 3b. Loop engine respects trust level ────────────────────────────
def test_loop_engine_confirm_short_circuits_on_l1():
    """L1 must end the loop at COMPLETED after the plan, without
    spawning the _run_pipeline task."""
    src = open("/app/backend/services/loop_engine.py").read()
    confirm_block = src.split("async def confirm(self, approved", 1)[1].split("async def confirm_ship(", 1)[0]
    assert "get_user_trust_level" in confirm_block
    assert 'level == "L1"' in confirm_block
    # L1 path must NOT spawn _run_pipeline.
    l1_path = confirm_block.split('level == "L1"', 1)[1].split("# L2 + L3", 1)[0]
    assert "_run_pipeline" not in l1_path, \
        "L1 must NOT execute the pipeline"
    # L1 path must release the lock so the user can re-run.
    assert "release_loop_lock" in l1_path
    # L1 must emit a COMPLETED event with a friendly upgrade message.
    assert "L1 report-only mode" in l1_path
    assert "trust_level" in l1_path


def test_loop_engine_auto_ships_on_l3():
    """L3 must skip the manual Ship gate and call confirm_ship(True)
    directly from _do_ship."""
    src = open("/app/backend/services/loop_engine.py").read()
    ship_block = src.split("async def _do_ship(", 1)[1].split("async def confirm_ship(", 1)[0]
    assert 'trust_level") == "L3"' in ship_block
    # Must call confirm_ship(True) — auto-ship.
    auto_path = ship_block.split('trust_level") == "L3"', 1)[1].split("self.state = LoopState.PAUSED_FOR_USER", 1)[0]
    assert "confirm_ship(True)" in auto_path
    assert "L3 auto-ship" in auto_path or "auto-ship" in auto_path
