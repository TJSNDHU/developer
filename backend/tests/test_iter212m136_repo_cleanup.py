"""
Iter 212m-136 — Repo cleanup pipeline (banner + bulk-delete endpoints).

Pinned behaviour:
  • GET  /cto/projects/cleanup-summary returns only persistently-broken
    projects (those whose `error` is in `_BROKEN_REASONS`).
  • POST /cto/projects/cleanup-delete re-verifies each submitted
    project is STILL broken before deleting (defence against a stale
    UI submitting a project the user just re-linked in another tab).
  • A `repo_cleanup_audit` row is written for every delete batch so the
    delete is traceable.
  • _CACHE entries for the deleted projects are popped so the sidebar
    doesn't render stale red rows on the next poll.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from routers import repo_status as rs


pytestmark = pytest.mark.asyncio


class _FakeCollection:
    def __init__(self, docs: list[dict] | None = None) -> None:
        self.docs = list(docs or [])
        self.inserted: list[dict] = []
        self.delete_calls: list[dict] = []

    def find(self, filt: dict, projection: dict | None = None):
        # Honour user_id + project_id filters (the only ones used).
        out = []
        for d in self.docs:
            if "user_id" in filt and d.get("user_id") != filt["user_id"]:
                continue
            pf = filt.get("project_id")
            if isinstance(pf, dict) and "$in" in pf:
                if d.get("project_id") not in set(pf["$in"]):
                    continue
            elif pf is not None and d.get("project_id") != pf:
                continue
            out.append({k: v for k, v in d.items() if k != "_id"})

        class _Cursor:
            def __init__(self, rows):
                self._rows = rows
            def sort(self, *_a, **_kw):
                return self
            def to_list(self, n):
                async def _coro():
                    return self._rows[:n]
                return _coro()
        return _Cursor(out)

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id="fake")

    async def delete_many(self, filt):
        pf = filt.get("project_id", {}).get("$in", [])
        before = len(self.docs)
        self.docs = [
            d for d in self.docs
            if not (d.get("user_id") == filt.get("user_id")
                    and d.get("project_id") in set(pf))
        ]
        self.delete_calls.append(filt)
        return SimpleNamespace(deleted_count=before - len(self.docs))


class _FakeDB:
    def __init__(self, projects: list[dict]) -> None:
        self.cto_projects = _FakeCollection(projects)
        self.dev_users = _FakeCollection([])
        self.repo_cleanup_audit = _FakeCollection([])

    # Compatibility with `dev_users.find_one`
    def __getattr__(self, name):
        if name == "dev_users":
            return self.dev_users
        raise AttributeError(name)


@pytest.fixture(autouse=True)
def _clear_cache():
    rs._CACHE.clear()
    yield
    rs._CACHE.clear()


async def _connection_status_stub(authorization: str):
    """Default stub for `connection_status` — returns a mix of broken
    + healthy projects so the cleanup endpoints have something to act
    on. Tests can monkeypatch this per-test for finer control."""
    return {
        "ok": True,
        "statuses": [
            {"project_id": "p_broken_1", "status": "disconnected",
             "error": "repo_not_found", "http_code": 404,
             "owner": "old", "repo": "missing"},
            {"project_id": "p_broken_2", "status": "disconnected",
             "error": "repo_not_set", "http_code": 0,
             "owner": "", "repo": ""},
            {"project_id": "p_healthy", "status": "connected",
             "http_code": 200, "owner": "user", "repo": "alive"},
            {"project_id": "p_transient", "status": "disconnected",
             "error": "network: ConnectionError", "http_code": 0,
             "owner": "user", "repo": "blip"},
        ],
        "checked_at": 1782754462.289053,
    }


def _make_db():
    return _FakeDB([
        {"project_id": "p_broken_1", "user_id": "u1",
         "name": "Old Project", "github_owner": "old",
         "github_repo": "missing", "branch": "main"},
        {"project_id": "p_broken_2", "user_id": "u1",
         "name": "Empty Project", "github_owner": "",
         "github_repo": "", "branch": ""},
        {"project_id": "p_healthy", "user_id": "u1",
         "name": "Live", "github_owner": "user",
         "github_repo": "alive", "branch": "main"},
        {"project_id": "p_transient", "user_id": "u1",
         "name": "Blip", "github_owner": "user",
         "github_repo": "blip", "branch": "main"},
    ])


async def test_cleanup_summary_returns_only_persistent_failures(
    monkeypatch,
):
    """Transient `network:*` errors must NOT appear in cleanup-summary
    — the auto-heal pipeline retries those automatically. Only
    `repo_not_found`, `github_rejected`, `repo_not_set`, `no_token`
    should be surfaced."""
    monkeypatch.setattr(rs, "current_dev",
                        AsyncMock(return_value={"user_id": "u1"}))
    monkeypatch.setattr(rs, "get_db", lambda: _make_db())
    monkeypatch.setattr(rs, "connection_status", _connection_status_stub)

    resp = await rs.cleanup_summary(authorization="Bearer x")
    assert resp["ok"] is True
    assert resp["count"] == 2
    pids = {r["project_id"] for r in resp["broken"]}
    assert pids == {"p_broken_1", "p_broken_2"}
    # The transient one must NOT be in the list.
    assert "p_transient" not in pids
    # The healthy one must NOT be in the list.
    assert "p_healthy" not in pids


async def test_cleanup_summary_hydrates_label_owner_repo(monkeypatch):
    """The banner needs human-readable labels — the endpoint must
    join the connection-status result with the project row data."""
    monkeypatch.setattr(rs, "current_dev",
                        AsyncMock(return_value={"user_id": "u1"}))
    monkeypatch.setattr(rs, "get_db", lambda: _make_db())
    monkeypatch.setattr(rs, "connection_status", _connection_status_stub)

    resp = await rs.cleanup_summary(authorization="Bearer x")
    by_pid = {r["project_id"]: r for r in resp["broken"]}
    assert by_pid["p_broken_1"]["name"] == "Old Project"
    assert by_pid["p_broken_1"]["owner"] == "old"
    assert by_pid["p_broken_1"]["repo"] == "missing"
    assert by_pid["p_broken_1"]["error"] == "repo_not_found"


async def test_cleanup_delete_requires_non_empty_list(monkeypatch):
    monkeypatch.setattr(rs, "current_dev",
                        AsyncMock(return_value={"user_id": "u1"}))
    monkeypatch.setattr(rs, "get_db", lambda: _make_db())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rs.cleanup_delete(body={"project_ids": []},
                                authorization="Bearer x")
    assert ei.value.status_code == 400


async def test_cleanup_delete_caps_at_50_per_batch(monkeypatch):
    monkeypatch.setattr(rs, "current_dev",
                        AsyncMock(return_value={"user_id": "u1"}))
    monkeypatch.setattr(rs, "get_db", lambda: _make_db())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rs.cleanup_delete(
            body={"project_ids": [f"p_{i}" for i in range(51)]},
            authorization="Bearer x",
        )
    assert ei.value.status_code == 400


async def test_cleanup_delete_reverifies_before_deleting(monkeypatch):
    """If the UI submits a project_id that is NO LONGER broken
    (e.g. the user re-linked the repo in another tab), the delete
    must SKIP it — not silently delete a working project."""
    db = _make_db()
    monkeypatch.setattr(rs, "current_dev",
                        AsyncMock(return_value={"user_id": "u1"}))
    monkeypatch.setattr(rs, "get_db", lambda: db)
    monkeypatch.setattr(rs, "connection_status", _connection_status_stub)

    resp = await rs.cleanup_delete(
        body={"project_ids": ["p_broken_1", "p_healthy"]},
        authorization="Bearer x",
    )
    assert resp["deleted"] == 1
    assert resp["skipped"] == 1
    # Healthy project must still be in the DB.
    remaining = await db.cto_projects.find({}).to_list(50)
    assert any(p["project_id"] == "p_healthy" for p in remaining)
    assert not any(p["project_id"] == "p_broken_1" for p in remaining)


async def test_cleanup_delete_writes_audit_row(monkeypatch):
    db = _make_db()
    monkeypatch.setattr(rs, "current_dev",
                        AsyncMock(return_value={"user_id": "u1"}))
    monkeypatch.setattr(rs, "get_db", lambda: db)
    monkeypatch.setattr(rs, "connection_status", _connection_status_stub)

    resp = await rs.cleanup_delete(
        body={"project_ids": ["p_broken_1", "p_broken_2"]},
        authorization="Bearer x",
    )
    assert resp["deleted"] == 2
    assert resp["audit_id"].startswith("cleanup_")
    audit_rows = db.repo_cleanup_audit.inserted
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row["audit_id"] == resp["audit_id"]
    assert row["user_id"] == "u1"
    assert set(row["project_ids"]) == {"p_broken_1", "p_broken_2"}
    # Snapshot must NOT contain encrypted PATs.
    for snap_row in row["snapshot"]:
        assert "github_token" not in snap_row


async def test_cleanup_delete_clears_status_cache(monkeypatch):
    """The connection-status TTL cache must be invalidated for deleted
    projects so the sidebar doesn't carry a stale red row for ~8s."""
    db = _make_db()
    rs._CACHE["p_broken_1"] = {"project_id": "p_broken_1",
                               "status": "disconnected",
                               "checked_at": 9999999999.0}
    monkeypatch.setattr(rs, "current_dev",
                        AsyncMock(return_value={"user_id": "u1"}))
    monkeypatch.setattr(rs, "get_db", lambda: db)
    monkeypatch.setattr(rs, "connection_status", _connection_status_stub)

    await rs.cleanup_delete(
        body={"project_ids": ["p_broken_1"]},
        authorization="Bearer x",
    )
    assert "p_broken_1" not in rs._CACHE


async def test_cleanup_delete_rejects_non_string_project_ids(monkeypatch):
    monkeypatch.setattr(rs, "current_dev",
                        AsyncMock(return_value={"user_id": "u1"}))
    monkeypatch.setattr(rs, "get_db", lambda: _make_db())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await rs.cleanup_delete(
            body={"project_ids": ["p_ok", 12345, ""]},
            authorization="Bearer x",
        )
    assert ei.value.status_code == 400
