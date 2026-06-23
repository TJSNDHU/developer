"""Iter 212m — `/admin/insights/user-patterns` endpoint contract tests.

These are source-pinned + integration-light. We assert:
  - route is registered on the admin router
  - aggregation handler exists and shapes the response correctly
  - admin guard fires (401 without auth)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import admin as admin_router


# ──────────────────────────────────────────────────────────────────
# Source-pin: route exists.
# ──────────────────────────────────────────────────────────────────


def test_user_patterns_route_registered():
    paths = {r.path for r in admin_router.router.routes}
    assert "/admin/insights/user-patterns" in paths


def test_user_patterns_handler_exists():
    assert hasattr(admin_router, "user_patterns_insights")
    assert callable(admin_router.user_patterns_insights)


# ──────────────────────────────────────────────────────────────────
# Aggregation behaviour — mock require_db + _require_admin.
# ──────────────────────────────────────────────────────────────────


class _AsyncCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, n):
        return list(self._docs)


class _PatternsCollection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, *_args, **_kwargs):
        return _AsyncCursor(self._docs)


class _DB:
    def __init__(self, docs):
        self.ora_patterns = _PatternsCollection(docs)


@pytest.mark.asyncio
async def test_user_patterns_handler_aggregates(monkeypatch):
    docs = [
        {"user_id": "u1", "hot_files": ["chat.py", "App.jsx"],
         "stack_signals": ["fastapi", "react"], "session_count": 4},
        {"user_id": "u2", "hot_files": ["chat.py", "server.py"],
         "stack_signals": ["fastapi", "mongo"], "session_count": 3},
        {"user_id": "u3", "hot_files": ["App.jsx"],
         "stack_signals": ["react"], "session_count": 2},
    ]

    # Patch the guard + db.
    async def _ok_guard(_authz):
        return {"user_id": "admin", "is_admin": True}
    monkeypatch.setattr(admin_router, "_require_admin", _ok_guard)
    monkeypatch.setattr(admin_router, "require_db", lambda: _DB(docs))

    res = await admin_router.user_patterns_insights(authorization="Bearer x")

    assert res["ok"] is True
    assert res["users_with_patterns"] == 3
    assert res["total_sessions"] == 9
    assert res["records"] == 3

    # Files: chat.py appears for u1+u2 → count 2. App.jsx u1+u3 → count 2.
    # server.py only u2 → 1.
    files = {f["file"]: f["user_count"] for f in res["top_files"]}
    assert files.get("chat.py") == 2
    assert files.get("App.jsx") == 2
    assert files.get("server.py") == 1

    # Stack: react=2, fastapi=2, mongo=1.
    stack = {s["signal"]: s["count"] for s in res["stack_distribution"]}
    assert stack.get("react") == 2
    assert stack.get("fastapi") == 2
    assert stack.get("mongo") == 1


@pytest.mark.asyncio
async def test_user_patterns_handler_empty(monkeypatch):
    """An empty collection must produce zeroed buckets, never crash."""
    async def _ok_guard(_authz):
        return {"user_id": "admin", "is_admin": True}
    monkeypatch.setattr(admin_router, "_require_admin", _ok_guard)
    monkeypatch.setattr(admin_router, "require_db", lambda: _DB([]))

    res = await admin_router.user_patterns_insights(authorization="Bearer x")
    assert res["ok"] is True
    assert res["top_files"] == []
    assert res["stack_distribution"] == []
    assert res["users_with_patterns"] == 0
    assert res["total_sessions"] == 0
    assert res["records"] == 0


@pytest.mark.asyncio
async def test_user_patterns_handler_skips_blank_paths(monkeypatch):
    """Empty / non-string entries in hot_files or stack_signals must be
    dropped silently, not counted as a real signal."""
    docs = [
        {"user_id": "u1", "hot_files": ["", None, "real.py", 42],
         "stack_signals": ["", None, "fastapi"], "session_count": 1},
    ]
    async def _ok_guard(_authz):
        return {"user_id": "admin", "is_admin": True}
    monkeypatch.setattr(admin_router, "_require_admin", _ok_guard)
    monkeypatch.setattr(admin_router, "require_db", lambda: _DB(docs))

    res = await admin_router.user_patterns_insights(authorization="Bearer x")
    files = {f["file"] for f in res["top_files"]}
    sigs  = {s["signal"] for s in res["stack_distribution"]}
    assert files == {"real.py"}
    assert sigs  == {"fastapi"}
