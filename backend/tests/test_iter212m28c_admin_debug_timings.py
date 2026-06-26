"""
Iter 212m-28c — Admin debug endpoint for repo_context_timings.

GET /api/aurem-dev/admin/debug/repo_context_timings
  - Admin-only (requires JWT with is_admin OR tier=founder).
  - Returns the 20 most recent timing samples sorted by ts desc.
  - JSON-safe (ObjectId → str, datetime → isoformat) — raw Mongo
    docs are NEVER returned per project rules.

Tests cover:
  - Route exists at the right path (inherits the /admin prefix).
  - Auth gate (_require_admin) is wired.
  - Mongo unavailability returns 503, not 500.
  - JSON safety: no raw ObjectId in returned shape.
"""
from __future__ import annotations

import os

ADMIN_PY = os.path.join(
    os.path.dirname(__file__), "..", "routers", "admin.py"
)


def test_admin_debug_endpoint_route_present():
    """Route literal must match the user's intent: lands at
    /api/aurem-dev/admin/debug/repo_context_timings because the
    router carries an /admin prefix."""
    src = open(ADMIN_PY).read()
    assert '@router.get("/debug/repo_context_timings")' in src
    assert "async def admin_debug_repo_context_timings(" in src


def test_admin_debug_endpoint_uses_require_admin_gate():
    """No anonymous access. Must call `_require_admin(authorization)`
    BEFORE touching the collection."""
    src = open(ADMIN_PY).read()
    idx = src.find("async def admin_debug_repo_context_timings(")
    assert idx != -1
    block = src[idx:idx + 1500]
    assert "_require_admin(authorization)" in block
    # The auth call must come before the DB read.
    gate_pos  = block.find("_require_admin(authorization)")
    query_pos = block.find("db.repo_context_timings.find(")
    assert 0 < gate_pos < query_pos


def test_admin_debug_endpoint_serialises_object_ids_and_datetimes():
    """Raw Mongo docs would crash the JSON encoder. Endpoint must
    coerce _id → str and ts → isoformat."""
    src = open(ADMIN_PY).read()
    idx = src.find("async def admin_debug_repo_context_timings(")
    block = src[idx:idx + 2000]
    assert 'd["_id"] = str(d.get("_id"))' in block
    assert 'd["ts"] = ts.isoformat()' in block


def test_admin_debug_endpoint_caps_at_20_samples():
    """The user's spec calls for last 20 samples — must keep that
    cap to avoid an accidental full-collection scan."""
    src = open(ADMIN_PY).read()
    idx = src.find("async def admin_debug_repo_context_timings(")
    block = src[idx:idx + 1500]
    assert '.sort("ts", -1).limit(20).to_list(20)' in block


def test_admin_debug_endpoint_returns_count_alongside_timings():
    """Response shape: `{timings: [...], count: int}` — count saves
    operators an extra `len()` call when scripting against this."""
    src = open(ADMIN_PY).read()
    idx = src.find("async def admin_debug_repo_context_timings(")
    block = src[idx:idx + 1500]
    assert 'return {"timings": timings, "count": len(timings)}' in block


def test_admin_debug_endpoint_handles_db_unavailable():
    """DB-down must surface as 503, NOT a generic 500. Same pattern
    the rest of admin.py uses for graceful Mongo failure."""
    src = open(ADMIN_PY).read()
    idx = src.find("async def admin_debug_repo_context_timings(")
    block = src[idx:idx + 1500]
    assert 'status_code=503, detail="database unavailable"' in block
