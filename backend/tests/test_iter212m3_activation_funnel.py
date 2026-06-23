"""Iter 212m-3 — Activation Funnel endpoint tests.

Covers the 5-step funnel computation (signed_up → connected_github →
added_project → sent_message → shipped_code), per-step conversion
rates, and biggest-dropoff detection. All I/O is mocked.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import admin as admin_router


class _AsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for d in self._docs:
            yield d

    async def to_list(self, n):
        return list(self._docs)


class _Coll:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, *_args, **_kwargs):
        return _AsyncCursor(self._docs)


class _FunnelDB:
    """In-memory stand-in for the Mongo handle inside the funnel route.
    Pass per-collection doc lists to drive behaviour."""

    def __init__(self, users=(), projects=(), sessions=(), tasks=()):
        self.dev_users     = _Coll(users)
        self.cto_projects  = _Coll(projects)
        self.chat_sessions = _Coll(sessions)
        self.cto_tasks     = _Coll(tasks)


async def _ok_guard(_authz):
    return {"user_id": "admin", "is_admin": True}


# ──────────────────────────────────────────────────────────────────
# Test 1 — route & handler exist.
# ──────────────────────────────────────────────────────────────────


def test_activation_funnel_route_registered():
    paths = {r.path for r in admin_router.router.routes}
    assert "/admin/insights/activation-funnel" in paths
    assert hasattr(admin_router, "activation_funnel")


# ──────────────────────────────────────────────────────────────────
# Test 2 — Happy path: 5-step funnel matches user counts.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activation_funnel_5_step_counts(monkeypatch):
    users = [
        {"user_id": "u1", "email": "alice@real.com",
         "github": {"id": 123, "access_token": "tok"}},
        {"user_id": "u2", "email": "bob@real.com",
         "github": {"login": "bob"}},
        {"user_id": "u3", "email": "carol@real.com"},  # no github
    ]
    # u1 + u2 have projects; u3 doesn't.
    projects = [{"user_id": "u1"}, {"user_id": "u2"}]
    # u1 sent a message; u2 + u3 didn't.
    sessions = [{"user_id": "u1", "turns": [{}, {}]}]
    # No one shipped code yet.
    tasks    = []

    monkeypatch.setattr(admin_router, "_require_admin", _ok_guard)
    monkeypatch.setattr(admin_router, "require_db",
                        lambda: _FunnelDB(users, projects, sessions, tasks))

    res = await admin_router.activation_funnel(authorization="Bearer x")
    assert res["ok"] is True

    funnel = res["funnel"]
    assert funnel["signed_up"]        == 3
    assert funnel["connected_github"] == 2
    assert funnel["added_project"]    == 2
    assert funnel["sent_message"]     == 1
    assert funnel["shipped_code"]     == 0

    steps = res["funnel_steps"]
    assert len(steps) == 5
    assert [s["key"] for s in steps] == [
        "signed_up", "connected_github", "added_project",
        "sent_message", "shipped_code",
    ]


# ──────────────────────────────────────────────────────────────────
# Test 3 — Conversion rates: pct_of_prev clamped to [0, 100].
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activation_funnel_conversion_rates(monkeypatch):
    users = [
        {"user_id": "u1", "email": "a@real.com", "github": {"id": 1}},
        {"user_id": "u2", "email": "b@real.com", "github": {"id": 2}},
        {"user_id": "u3", "email": "c@real.com"},
        {"user_id": "u4", "email": "d@real.com"},
    ]
    # 2 of 4 connected github → 50.0%.
    projects = [{"user_id": "u1"}]   # 1 of 2 → 50.0%.
    sessions = []                     # 0 of 1 → 0.0%.
    tasks    = []                     # 0 of 0 → 0.0% (zero-prev guard).

    monkeypatch.setattr(admin_router, "_require_admin", _ok_guard)
    monkeypatch.setattr(admin_router, "require_db",
                        lambda: _FunnelDB(users, projects, sessions, tasks))

    res = await admin_router.activation_funnel(authorization="Bearer x")
    steps = res["funnel_steps"]
    # signed_up
    assert steps[0]["pct_of_prev"] == 100.0
    # connected_github = 2 of 4
    assert steps[1]["pct_of_prev"] == 50.0
    # added_project = 1 of 2
    assert steps[2]["pct_of_prev"] == 50.0
    # sent_message = 0 of 1
    assert steps[3]["pct_of_prev"] == 0.0
    # shipped_code = 0 of 0 → clamped to 0 (zero-prev guard).
    assert steps[4]["pct_of_prev"] == 0.0


# ──────────────────────────────────────────────────────────────────
# Test 4 — Biggest drop-off detection.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activation_funnel_biggest_dropoff(monkeypatch):
    # 10 signups, 9 github, 3 project (huge drop here), 3 sent, 2 shipped.
    users = [
        {"user_id": f"u{i}", "email": f"u{i}@real.com",
         "github": {"id": i} if i < 10 else None}
        for i in range(1, 11)
    ]
    # Drop github for u10.
    users[9]["github"] = None
    projects = [{"user_id": f"u{i}"} for i in range(1, 4)]    # 3 projects
    sessions = [{"user_id": f"u{i}", "turns": [{}]} for i in range(1, 4)]
    tasks    = [{"user_id": "u1"}, {"user_id": "u2"}]

    monkeypatch.setattr(admin_router, "_require_admin", _ok_guard)
    monkeypatch.setattr(admin_router, "require_db",
                        lambda: _FunnelDB(users, projects, sessions, tasks))

    res = await admin_router.activation_funnel(authorization="Bearer x")
    steps = res["funnel_steps"]
    # Drops:
    #   step1 (github):  10 - 9 = 1
    #   step2 (proj):    9 - 3  = 6  ← biggest
    #   step3 (sess):    3 - 3  = 0
    #   step4 (ship):    3 - 2  = 1
    assert res["biggest_dropoff_idx"] == 2
    assert steps[2]["is_biggest_dropoff"] is True
    assert steps[1]["is_biggest_dropoff"] is False
    # All other steps must not be flagged.
    flagged = [i for i, s in enumerate(steps) if s["is_biggest_dropoff"]]
    assert flagged == [2]


# ──────────────────────────────────────────────────────────────────
# Test 5 — Test/automation accounts excluded from real count.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activation_funnel_excludes_test_accounts(monkeypatch):
    users = [
        {"user_id": "real1", "email": "alice@gmail.com", "github": {"id": 1}},
        {"user_id": "test1", "email": "test@aurem.dev",  "github": {"id": 2}},  # excluded
        {"user_id": "qa1",   "email": "qa-prod@aurem.dev"},                      # excluded
        {"user_id": "audit", "email": "audit_xx@aurem.dev"},                     # excluded
        {"user_id": "synth", "email": "u_abcdef123@aurem.test"},                 # excluded
    ]
    monkeypatch.setattr(admin_router, "_require_admin", _ok_guard)
    monkeypatch.setattr(admin_router, "require_db",
                        lambda: _FunnelDB(users, [], [], []))

    res = await admin_router.activation_funnel(authorization="Bearer x")
    assert res["funnel"]["signed_up"]        == 1
    assert res["funnel"]["connected_github"] == 1   # only real1
    assert res["totals"]["all_users"] == 5
    assert res["totals"]["test_users_excluded"] == 4


# ──────────────────────────────────────────────────────────────────
# Bonus — Empty db: handler must not crash.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activation_funnel_empty_db(monkeypatch):
    monkeypatch.setattr(admin_router, "_require_admin", _ok_guard)
    monkeypatch.setattr(admin_router, "require_db", lambda: _FunnelDB([], [], [], []))

    res = await admin_router.activation_funnel(authorization="Bearer x")
    assert res["ok"] is True
    # All counts zero, no biggest-dropoff (no users dropped because no
    # users entered the funnel).
    for s in res["funnel_steps"]:
        assert s["count"] == 0
    assert res["biggest_dropoff_idx"] is None
