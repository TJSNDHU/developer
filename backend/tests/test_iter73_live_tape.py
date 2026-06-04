"""
test_iter73_live_tape.py — Iter 73 Task 1: live worker tape (SSE).

Locks:
  • _emit() drops a structured frame onto the per-task queue
  • _log() also fans out to the SSE queue (kind mapped from status)
  • Queue overflow drops the OLDEST frame, never blocks the worker
  • /cto/tasks/{id}/stream returns a SSE response with terminal frame
    when the task already completed (most reliable path to assert
    end-to-end SSE without spinning the real GitHub pipeline)
  • Frontend TaskLiveTape component exists with the required testids
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, patch

import pytest


def _read(rel: str) -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── _emit() / queue ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_puts_frame_on_queue():
    from routers import cto_projects as m
    tid = f"test-{time.time_ns()}"
    m._task_queues.pop(tid, None)

    await m._emit(tid, "Reading repository files…", pct=10)
    await m._emit(tid, f"Done — abc1234", kind="done", pct=100)

    q = m._task_queues[tid]
    a = q.get_nowait()
    b = q.get_nowait()
    assert a["step"] == "Reading repository files…"
    assert a["pct"] == 10
    assert a["type"] == "step"
    assert "ts" in a and isinstance(a["ts"], float)

    assert b["type"] == "done"
    assert b["pct"] == 100
    assert b["step"].startswith("Done — ")
    m._task_queues.pop(tid, None)


@pytest.mark.asyncio
async def test_log_fans_out_to_sse_queue():
    """_log() writes to Mongo AND pushes onto the SSE queue."""
    from routers import cto_projects as m

    tid = f"test-{time.time_ns()}"
    m._task_queues.pop(tid, None)

    # Stub the DB call so we don't need a live Mongo for the fanout test.
    with patch.object(m, "get_db", return_value=None):
        await m._log(tid, "🧠 DeepSeek thinking…")
        await m._log(tid, "❌ something blew up", "error")

    q = m._task_queues[tid]
    a = q.get_nowait()
    b = q.get_nowait()
    assert a["step"] == "🧠 DeepSeek thinking…"
    assert a["type"] == "step"
    # error → fail mapping
    assert b["step"].startswith("❌")
    assert b["type"] == "fail"
    m._task_queues.pop(tid, None)


@pytest.mark.asyncio
async def test_emit_queue_overflow_drops_oldest_not_worker():
    """A slow SSE consumer must never block the worker."""
    from routers import cto_projects as m

    tid = f"overflow-{time.time_ns()}"
    m._task_queues.pop(tid, None)
    q = asyncio.Queue(maxsize=4)
    m._task_queues[tid] = q

    # Push 10 frames into a queue that only holds 4.
    for i in range(10):
        await m._emit(tid, f"step {i}", pct=i * 10)

    assert q.qsize() == 4
    # The OLDEST should have been dropped — first remaining should be
    # one of the later steps, not "step 0".
    first = q.get_nowait()
    assert first["step"] != "step 0"
    m._task_queues.pop(tid, None)


# ── /cto/tasks/{id}/stream endpoint shape ────────────────────────────

def test_stream_endpoint_registered():
    """The new SSE route is mounted on the cto router."""
    from routers.cto_projects import router
    paths = {r.path for r in router.routes}
    assert "/cto/tasks/{task_id}/stream" in paths


@pytest.mark.asyncio
async def test_stream_endpoint_synthesizes_terminal_frame_for_completed_task():
    """If a client connects AFTER a task already finished, the endpoint
    must emit one synthetic terminal frame and close — no infinite ping
    loop on done tasks."""
    from routers import cto_projects as m

    tid = "done-task-xyz"
    fake_task = {
        "task_id": tid, "user_id": "u1",
        "status": "done", "commit_sha": "deadbeefcafe",
    }
    fake_db = type("DB", (), {
        "cto_tasks": type("Col", (), {
            "find_one": AsyncMock(return_value=fake_task),
        })(),
    })()

    with patch.object(m, "current_dev", AsyncMock(return_value={"user_id": "u1"})), \
         patch.object(m, "require_db", return_value=fake_db):
        resp = await m.task_stream(task_id=tid, authorization="Bearer fake")
    # StreamingResponse — pull the generator and assert content.
    frames = []
    async for chunk in resp.body_iterator:
        frames.append(
            chunk.decode() if isinstance(chunk, (bytes, bytearray)) else chunk
        )
    body = "".join(frames)
    assert body.startswith("data: ")
    payload = json.loads(body.split("data: ", 1)[1].split("\n\n", 1)[0])
    assert payload["type"] == "done"
    assert payload["step"].startswith("Done — deadbee")
    assert payload["pct"] == 100


# ── Frontend wiring ───────────────────────────────────────────────────

def test_task_live_tape_component_exists_with_testids():
    js = _read("frontend/src/components/TaskLiveTape.jsx")
    # Must hit the new SSE endpoint
    assert "/cto/tasks/" in js and "/stream" in js
    # Must have stable testids for the testing agent
    assert 'data-testid="task-live-tape"' in js
    assert "task-live-tape-bar" in js
    assert "task-live-tape-step-" in js
    assert "task-live-tape-caret" in js


def test_task_live_tape_wired_into_message_bubble():
    js = _read("frontend/src/components/MessageBubble.jsx")
    assert "TaskLiveTape" in js
    assert 'import TaskLiveTape from "./TaskLiveTape";' in js


def test_aurem_blink_keyframe_in_index_css():
    css = _read("frontend/src/index.css")
    assert "@keyframes aurem-blink" in css
