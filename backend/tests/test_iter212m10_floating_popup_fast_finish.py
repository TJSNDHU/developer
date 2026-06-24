"""Iter 212m-10 — Floating LiveTaskPopup fast-finish task_handoff frame.

Bug: When a CTO task finishes BEFORE the SSE client connects (very
common for 1-2 s commits), `routers/cto_projects.py::task_stream`
synthesises a single `done` frame from Mongo and exits — skipping
the `task_handoff` frame entirely. The chat bubble's <TaskLiveTape>
relays `task_handoff` → `ora-task-handoff` window event →
ChatPanel `setLivePopupTaskId(tid)` → floating popup mounts. With
the handoff frame missing, the popup never surfaces for quick
ships, which the founder reported as "popup window show hoti thi
ab nahi ho rahi".

Fix: emit a synthetic `task_handoff` frame BEFORE the synthetic
`done` frame in both fast-finish branches (early-terminal + the
heartbeat poll branch).

This test locks the fix so the floating popup never silently
breaks again for fast tasks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import cto_projects as ctop  # noqa: E402


async def _mock_current_dev(_authz):
    return {"user_id": "u_test", "email": "t@test.com"}


def _fake_db_with_task(task_doc):
    db = MagicMock()
    db.cto_tasks.find_one = AsyncMock(return_value=task_doc)
    return db


# ──────────────────────────────────────────────────────────────────
# Branch A — task already in `done` state when client connects.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fast_finish_emits_synthetic_task_handoff_before_done():
    """The most common path: user submits a 1-2 s ship; by the time
    the chat bubble's <TaskLiveTape> opens the SSE stream the worker
    has already written `status=done` to Mongo. The handler MUST emit
    `task_handoff` first, then `done` — otherwise the floating popup
    never latches on."""
    task = {
        "task_id":    "t_quick",
        "user_id":    "u_test",
        "status":     "done",
        "commit_sha": "abc12345678",
        "project_id": "p_app1",
    }
    db = _fake_db_with_task(task)

    # Drain the SSE generator into a flat list of frames.
    with patch.object(ctop, "current_dev", _mock_current_dev), \
         patch.object(ctop, "require_db", return_value=db):
        resp = await ctop.task_stream("t_quick", "Bearer x")

    frames = []
    async for chunk in resp.body_iterator:
        # Strip "data: " + parse JSON
        for line in chunk.split("\n\n"):
            line = line.strip()
            if line.startswith("data:"):
                frames.append(json.loads(line[5:].strip()))

    types = [f.get("type") for f in frames]
    assert "task_handoff" in types, (
        "synthetic task_handoff must be emitted on fast-finish "
        f"(got types={types})"
    )
    assert "done" in types
    # Order matters — handoff must precede done so the popup mounts
    # while the bubble is still showing the completion tape.
    assert types.index("task_handoff") < types.index("done")

    handoff = next(f for f in frames if f["type"] == "task_handoff")
    assert handoff["project_id"] == "p_app1"
    assert handoff["sha"] == "abc1234"   # 7-char prefix
    assert handoff["source"] == "task_stream_synthetic"


@pytest.mark.asyncio
async def test_fast_finish_failed_does_NOT_emit_task_handoff():
    """Failed tasks should NOT trigger the popup (no commit to view).
    Only `fail` frame, no synthetic handoff."""
    task = {
        "task_id": "t_failed", "user_id": "u_test",
        "status":  "failed", "error": "boom", "project_id": "p_app1",
    }
    db = _fake_db_with_task(task)

    with patch.object(ctop, "current_dev", _mock_current_dev), \
         patch.object(ctop, "require_db", return_value=db):
        resp = await ctop.task_stream("t_failed", "Bearer x")

    frames = []
    async for chunk in resp.body_iterator:
        for line in chunk.split("\n\n"):
            line = line.strip()
            if line.startswith("data:"):
                frames.append(json.loads(line[5:].strip()))

    types = [f.get("type") for f in frames]
    assert "task_handoff" not in types
    assert "fail" in types


# ──────────────────────────────────────────────────────────────────
# Branch B — task transitions to done during the heartbeat poll.
# (Live-connect path: client attaches mid-flight, worker finishes,
#  queue is empty, the 2 s heartbeat Mongo-poll catches the
#  status change.)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_branch_emits_synthetic_task_handoff_before_done():
    """When the queue is empty and a heartbeat-poll discovers the
    task just finished, the same `task_handoff`-before-`done`
    ordering MUST hold."""
    # 1st find_one (route auth): returns `running` so we enter the
    # live loop. 2nd find_one (heartbeat poll): returns `done`.
    task_running = {
        "task_id": "t_mid", "user_id": "u_test",
        "status":  "running", "project_id": "p_app1",
    }
    task_finished = {
        "task_id": "t_mid", "user_id": "u_test",
        "status":  "done", "commit_sha": "deadbeef999",
        "project_id": "p_app1",
    }

    db = MagicMock()
    db.cto_tasks.find_one = AsyncMock(side_effect=[task_running,
                                                    task_finished])

    # Make the queue empty so the heartbeat path is exercised on the
    # very first iteration (timeout=2 s default → patched to 0.01 s).
    if "t_mid" in ctop._task_queues:
        del ctop._task_queues["t_mid"]

    # Patch the asyncio.wait_for that wraps q.get() so it raises
    # TimeoutError immediately instead of waiting 2 s.
    orig_wait_for = ctop.asyncio.wait_for

    async def _instant_timeout(coro, timeout):  # noqa: ARG001
        # Cancel the coro and raise TimeoutError so the heartbeat
        # branch runs straight away.
        try:
            coro.close()
        except Exception:
            pass
        raise ctop.asyncio.TimeoutError

    with patch.object(ctop, "current_dev", _mock_current_dev), \
         patch.object(ctop, "require_db", return_value=db), \
         patch.object(ctop.asyncio, "wait_for", _instant_timeout):
        resp = await ctop.task_stream("t_mid", "Bearer x")

        frames = []
        async for chunk in resp.body_iterator:
            for line in chunk.split("\n\n"):
                line = line.strip()
                if line.startswith("data:"):
                    frames.append(json.loads(line[5:].strip()))
        # Restore original
        ctop.asyncio.wait_for = orig_wait_for

    types = [f.get("type") for f in frames]
    assert "task_handoff" in types, (
        "heartbeat-branch must mint a synthetic task_handoff "
        f"(got types={types})"
    )
    assert "done" in types
    assert types.index("task_handoff") < types.index("done")
