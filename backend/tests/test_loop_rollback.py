"""Iter 329 · tests for services/loop_rollback.py

Verifies the rollback worker's persistence contract WITHOUT hitting
GitHub — we stub gh_api_revert with a fake awaitable and assert:
  1. Success path persists rollback_status="done" + rollback_sha +
     rollback_html_url + rollback_completed_at.
  2. Failure path persists rollback_status="failed" + rollback_error
     with the PAT scrubbed.
  3. Fail-open: worker never raises regardless of underlying error.
  4. Steps are appended to rollback_steps array + mirrored to
     loop_run_log with kind="loop_rollback_step".
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import loop_rollback


class _FakeDB:
    """Tiny in-memory stub — records .update_one and .insert_one calls."""

    def __init__(self):
        self.loop_sessions = MagicMock()
        self.loop_run_log  = MagicMock()
        self.loop_sessions.update_one = AsyncMock(return_value=None)
        self.loop_run_log.insert_one  = AsyncMock(return_value=None)
        # Convenience for test assertions.
        self.set_calls = []       # list of {**fields} passed to $set
        self.push_calls = []      # list of $push payloads
        self.insert_calls = []    # list of loop_run_log docs

        async def _upd(filt, ops):
            if "$set" in ops:
                self.set_calls.append(ops["$set"])
            if "$push" in ops:
                self.push_calls.append(ops["$push"])
        self.loop_sessions.update_one.side_effect = _upd

        async def _ins(doc):
            self.insert_calls.append(doc)
        self.loop_run_log.insert_one.side_effect = _ins


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def stub_revert_ok(monkeypatch):
    """gh_api_revert returns a fake success payload."""
    from services import github_api_writer as gw
    async def _fake(**kw):
        # Emulate a real revert commit response.
        return {"sha": "abc1234deadbeef", "html_url":
                "https://github.com/x/y/commit/abc1234deadbeef"}
    monkeypatch.setattr(gw, "revert_commit", _fake)


@pytest.fixture
def stub_revert_fail(monkeypatch):
    """gh_api_revert always raises — with PAT text embedded so we can
    verify the scrubber."""
    from services import github_api_writer as gw
    async def _fake(**kw):
        # Deliberately embed the fake PAT so we can prove scrubbing.
        raise RuntimeError("boom · leaked_pat=ghp_FAKEPAT")
    monkeypatch.setattr(gw, "revert_commit", _fake)


@pytest.fixture
def stub_identity(monkeypatch):
    from services import git_identity as gi
    async def _fake(db, user_id):
        return ("Test Dev", "dev@example.com")
    monkeypatch.setattr(gi, "resolve_git_identity", _fake)


@pytest.mark.asyncio
async def test_success_path_persists_done_row(
    fake_db, stub_revert_ok, stub_identity,
):
    await loop_rollback.run_rollback(
        db=fake_db, loop_id="loop_iter329_ok",
        project={"github_owner": "ownerx", "github_repo": "repox",
                  "branch": "main", "user_id": "u1"},
        commit_sha="deadbeef1234567",
        user_token="ghp_FAKEPAT",
    )
    # Iter 344 — the SSE emit path (Iter 330) writes `last_event`
    # $sets to loop_sessions interleaved with the rollback_status
    # $sets, so positional indexing broke. Filter to the status
    # writes: first must be "running", last "done".
    status_sets = [s for s in fake_db.set_calls if "rollback_status" in s]
    running = status_sets[0]
    assert running["rollback_status"] == "running"
    assert running["rollback_commit_sha"] == "deadbeef1234567"

    done = status_sets[-1]
    assert done["rollback_status"] == "done"
    assert done["rollback_sha"] == "abc1234deadbeef"
    assert done["rollback_html_url"].endswith("/commit/abc1234deadbeef")
    assert "rollback_completed_at" in done


@pytest.mark.asyncio
async def test_failure_path_persists_failed_and_scrubs_pat(
    fake_db, stub_revert_fail, stub_identity,
):
    await loop_rollback.run_rollback(
        db=fake_db, loop_id="loop_iter329_fail",
        project={"github_owner": "ownerx", "github_repo": "repox",
                  "branch": "main", "user_id": "u1"},
        commit_sha="deadbeef1234567",
        user_token="ghp_FAKEPAT",
    )
    failed = [s for s in fake_db.set_calls if "rollback_status" in s][-1]
    assert failed["rollback_status"] == "failed"
    err = failed["rollback_error"]
    # PAT must have been scrubbed out.
    assert "ghp_FAKEPAT" not in err
    assert "***PAT***" in err
    assert "boom" in err
    assert "rollback_completed_at" in failed


@pytest.mark.asyncio
async def test_worker_never_raises(fake_db, monkeypatch):
    """Even if BOTH the revert AND our error handler somehow blow up,
    the worker must return None. Simulate a catastrophic revert
    exception."""
    from services import github_api_writer as gw
    async def _fake(**kw):
        raise RuntimeError("totally broken")
    monkeypatch.setattr(gw, "revert_commit", _fake)

    # Even without identity stubbing, must not raise.
    ret = await loop_rollback.run_rollback(
        db=fake_db, loop_id="loop_iter329_catastrophe",
        project={"github_owner": "o", "github_repo": "r",
                  "branch": "main"},
        commit_sha="x", user_token=None,
    )
    assert ret is None


@pytest.mark.asyncio
async def test_steps_mirror_to_loop_run_log(
    fake_db, stub_revert_ok, stub_identity,
):
    await loop_rollback.run_rollback(
        db=fake_db, loop_id="loop_iter329_steps",
        project={"github_owner": "o", "github_repo": "r",
                  "branch": "main", "user_id": "u1"},
        commit_sha="c" * 40, user_token="ghp_x",
    )
    # Every step logged should also produce a loop_run_log insert
    # with the new kind so SYSTEM_INVENTORY can discover it.
    kinds = {d.get("kind") for d in fake_db.insert_calls}
    assert "loop_rollback_step" in kinds
    # rollback_steps array must have at least one entry.
    assert any("rollback_steps" in p for p in fake_db.push_calls)
