"""Iter 132 regression: Mode C ship-shortcut MUST emit periodic
`{thinking:true, elapsed_s, activity}` SSE frames while
`_enqueue_cto_task` is running.

User report: "our system not fixing just thinking thinking and also
not showing time tooo". Root cause: the ship-shortcut path bypassed
the `_ticker()` loop that the normal `chat_with_tools` flow uses, so
the UI saw "Thinking…" with no elapsed counter while the GitHub /
repo validation work happened.

This test drives `_maybe_ship_shortcut` end-to-end with a slow stubbed
`_enqueue_cto_task` and asserts the SSE byte stream contains AT LEAST
one tick frame between the initial meta and the final `done` frame.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest


@pytest.mark.asyncio
async def test_ship_shortcut_emits_thinking_ticks(monkeypatch):
    # Stub mongo: a chat_session with an aurem-handoff fence on the
    # most recent assistant turn.
    fake_handoff = "```aurem-handoff\nFix the README typo.\n```"
    fake_session = {
        "messages": [
            {"role": "user", "content": "hey"},
            {"role": "assistant", "content": "Sure. " + fake_handoff},
        ],
    }

    class _Coll:
        async def find_one(self, *a, **kw):
            return fake_session

        async def update_one(self, *a, **kw):
            return None

    class _DB:
        chat_sessions = _Coll()
        cto_tasks = _Coll()

    from cto_services import db as db_mod
    monkeypatch.setattr(db_mod, "get_db", lambda: _DB(), raising=True)

    # Stub the slow enqueue. Sleeps 1.6s so multiple ticks (~0.5s apart)
    # must be interleaved.
    async def _slow_enqueue(*, user_id, project_id, task_text):
        await asyncio.sleep(1.6)
        return {"ok": True, "task_id": "task_test_123", "project_id": project_id}

    # Inject a fake routers.cto_projects module exposing _enqueue_cto_task.
    fake_mod = types.ModuleType("routers.cto_projects")
    fake_mod._enqueue_cto_task = _slow_enqueue
    monkeypatch.setitem(sys.modules, "routers.cto_projects", fake_mod)

    # Build a minimal body object the helper expects.
    class _Body:
        prompt = "ship"
        session_id = "s1"
        project_id = "proj_test"

    from routers import chat as chat_router
    monkeypatch.setattr(chat_router, "get_db", lambda: _DB(), raising=True)

    gen = await chat_router._maybe_ship_shortcut(
        body=_Body(), user_id="u1", repo_ctx="",
    )
    assert gen is not None, "shortcut should match prompt='ship'"

    frames: list[dict] = []
    async for chunk in gen:
        # Each chunk is a single SSE frame: "data: {...}\n\n"
        line = chunk.strip()
        if not line.startswith("data:"):
            continue
        frames.append(json.loads(line[5:].strip()))

    # Sanity: meta first, done last
    assert frames[0].get("meta") is True, "first frame must be meta"
    assert frames[-1].get("done") is True, "last frame must be done"

    # Must contain at least 2 thinking ticks (instant + during 1.6s wait)
    ticks = [f for f in frames if f.get("thinking") is True]
    assert len(ticks) >= 2, (
        f"expected ≥2 tick frames during ship shortcut, got {len(ticks)}: "
        f"{ticks}"
    )

    # Tick frames must carry numeric elapsed_s and a string activity.
    for t in ticks:
        assert isinstance(t.get("elapsed_s"), (int, float)), t
        assert isinstance(t.get("activity"), str) and t["activity"], t

    # Elapsed must be monotonically non-decreasing across ticks.
    elapsed_values = [t["elapsed_s"] for t in ticks]
    assert elapsed_values == sorted(elapsed_values), (
        f"tick elapsed_s went backwards: {elapsed_values}"
    )

    # And the task_handoff frame must still be emitted (iter 125 regression).
    handoffs = [f for f in frames if f.get("type") == "task_handoff"]
    assert handoffs, "ship shortcut must still emit task_handoff frame"
    assert handoffs[0]["task_id"] == "task_test_123"
