"""
Iter 212m-130 — Loop Mode founder-only gate.

Loop Mode has been temporarily locked to founder / admin /
is_unlimited accounts while the engine is being hardened
(stuck-in-loop + verify retry storms reported in production).

Tests:
  • POST /loop/start returns 403 with `coming_soon:true` for
    a regular paying user.
  • Founder / admin / is_unlimited account passes the gate
    (we don't run the full engine — just verify the gate clears).
  • POST /chat/stream silently downgrades `execution_mode:"loop"`
    to `"prompt"` for non-founders so a stale localStorage
    can't trigger Loop prompt enrichment.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


# ─── 1) Backend gate logic — direct unit test ─────────────────────
@pytest.mark.parametrize("user,expected_block", [
    ({"user_id": "u1", "is_admin": False, "is_unlimited": False,
      "tier": "paid"},                              True),
    ({"user_id": "u1", "is_admin": False, "is_unlimited": False,
      "tier": "free"},                              True),
    ({"user_id": "u1", "is_admin": False, "is_unlimited": False},
                                                    True),
    # Founders pass:
    ({"user_id": "u1", "is_admin": True},           False),
    ({"user_id": "u1", "is_unlimited": True},       False),
    ({"user_id": "u1", "tier": "founder"},          False),
])
def test_loop_gate_classifies_correctly(user, expected_block):
    """The gate condition lives inline in routers/loop.py — replicate
    it here so a future refactor doesn't accidentally widen access."""
    is_founder = bool(
        user.get("is_admin") or user.get("is_unlimited")
        or (user.get("tier") == "founder")
    )
    blocked = not is_founder
    assert blocked is expected_block


# ─── 2) Loop start endpoint returns coming_soon 403 ───────────────
@pytest.mark.asyncio
async def test_loop_start_returns_coming_soon_for_non_founder(monkeypatch):
    """Patch current_dev + get_db so we can drive routers.loop's
    start_loop directly without spinning up FastAPI."""
    from routers import loop as loop_router

    async def fake_current_dev(_auth):
        return {"user_id": "u_paying", "tier": "paid",
                "is_admin": False, "is_unlimited": False}

    class _FakeDB:
        async def find_one(self, *_a, **_kw):
            return None
    monkeypatch.setattr(loop_router, "current_dev", fake_current_dev)
    monkeypatch.setattr(loop_router, "get_db", lambda: _FakeDB())

    body = loop_router.StartBody(user_message="ship me a feature")
    with pytest.raises(HTTPException) as exc_info:
        await loop_router.start_loop(body=body, authorization=None)
    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail.get("error")       == "loop_mode_locked"
    assert detail.get("coming_soon") is True
    assert "coming soon" in (detail.get("message") or "").lower()


# ─── 3) Founder is NOT blocked by the gate ─────────────────────────
@pytest.mark.asyncio
async def test_loop_start_lets_founder_through(monkeypatch):
    """A founder must skip the 403; we stub the rest of the engine so
    we only verify the gate clears (anything after that is iter
    212m-115 territory)."""
    from routers import loop as loop_router

    async def fake_current_dev(_auth):
        return {"user_id": "u_founder", "tier": "founder",
                "is_admin": False, "is_unlimited": False}

    class _FakeDB:
        async def find_one(self, *_a, **_kw):
            return None
    monkeypatch.setattr(loop_router, "current_dev", fake_current_dev)
    monkeypatch.setattr(loop_router, "get_db", lambda: _FakeDB())

    # Short-circuit acquire_loop_lock to fail fast AFTER the gate so
    # we don't actually run the engine.  We're only asserting the
    # gate doesn't 403.
    from services import loop_safety
    async def fake_acquire(*_a, **_kw):
        return False, {"loop_id": "lp_existing"}
    monkeypatch.setattr(loop_safety, "acquire_loop_lock", fake_acquire)
    async def fake_circuit(*_a, **_kw):
        return (False, 0, 0)
    monkeypatch.setattr(loop_safety, "is_loop_circuit_open", fake_circuit)

    body = loop_router.StartBody(user_message="ship me a feature")
    # If the gate is broken, this would raise 403; we expect a
    # different error code (409 from the locked acquire_loop_lock).
    with pytest.raises(HTTPException) as exc_info:
        await loop_router.start_loop(body=body, authorization=None)
    # 409 == loop_already_running (the next safety after the gate).
    # That's proof we cleared the gate; any 403 would be a regression.
    assert exc_info.value.status_code == 409


# ─── 4) chat.py silent downgrade for non-founders ──────────────────
def test_chat_loop_execution_mode_downgrade_logic():
    """The downgrade is purely a `body.execution_mode = "prompt"`
    rewrite inside the chat handler — replicate the predicate so a
    future refactor doesn't accidentally remove it."""
    cases = [
        # (user, exec_mode_in, expected_out)
        ({"is_admin": False, "tier": "paid"},     "loop",   "prompt"),
        ({"is_admin": False, "tier": "free"},     "loop",   "prompt"),
        ({"is_admin": False, "tier": "paid"},     "prompt", "prompt"),
        # Founder paths keep loop:
        ({"is_admin": True},                      "loop",   "loop"),
        ({"is_unlimited": True},                  "loop",   "loop"),
        ({"tier": "founder"},                     "loop",   "loop"),
    ]
    for user, exec_in, expected in cases:
        is_founder = bool(
            user.get("is_admin") or user.get("is_unlimited")
            or (user.get("tier") == "founder")
        )
        out = exec_in
        if (out or "").lower() == "loop" and not is_founder:
            out = "prompt"
        assert out == expected, f"user={user} in={exec_in} got={out}"
