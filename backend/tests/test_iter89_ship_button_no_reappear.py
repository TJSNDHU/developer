"""
test_iter89_ship_button_no_reappear — strict "ship button never returns"

User-reported bug:
  After shipping a fence successfully, refreshing the page (or logging
  out and back in) made the Ship-via-CTO button reappear on the same
  assistant message. It should be gone forever the moment a turn is
  marked shipped.

Root cause:
  MessageBubble.extractHandoffBrief() always ran on raw m.content,
  unaware that the turn was already shipped. On reload the assistant
  turn arrives with shipped_task_id from /chat/history BUT the raw
  content still contains the ```aurem-handoff fence, so the brief
  extracted → ShipDialog rendered → button row visible while the
  internal "shipped" state caught up.

Fix:
  MessageBubble.jsx — gate the brief extraction on `!m.shipped_task_id`.
  Once a turn has been shipped, the brief is suppressed entirely and
  render-path B (TaskLiveTape standalone) takes over. The Ship button
  cannot return.

Also locks the backend persistence chain:
  • /chat/turn/shipped persists shipped_task_id on the turn document.
  • /chat/history returns shipped_task_id on each turn so the UI can
    suppress the button on first render after reload.
"""
from __future__ import annotations

import os
import re
import time
import uuid

import httpx
import pytest


API = "http://localhost:8001/api/aurem-dev"
FOUNDER_EMAIL = "teji.ss1986@gmail.com"
FOUNDER_PASSWORD = "founder-test-pass-9281"
BASE = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(rel: str) -> str:
    with open(os.path.join(BASE, rel), encoding="utf-8") as fh:
        return fh.read()


# ── 1. UI guard — extractHandoffBrief gated on shipped_task_id ────────

def test_handoff_brief_suppressed_once_shipped():
    """The MessageBubble must NOT extract a handoff brief when the
    message already has a shipped_task_id. Catches the regression
    where the button reappeared after refresh."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    # The gated form must exist (showActions AND !m.shipped_task_id).
    assert "showActions && !m.shipped_task_id" in src, (
        "extractHandoffBrief must be gated on `!m.shipped_task_id` so "
        "the Ship button can't reappear after the turn was shipped"
    )
    # The Iter 89 rationale comment must persist so a future refactor
    # doesn't quietly drop the guard.
    assert "Iter 89" in src


def test_old_unconditional_extract_call_is_gone():
    """The previous unconditional call site (no shipped guard) must
    not exist anymore — otherwise both paths run and the button
    races back in."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    # The old line was a bare ternary on showActions only.
    bad = re.search(
        r"const handoffBrief\s*=\s*showActions\s*\?\s*extractHandoffBrief",
        src,
    )
    assert not bad, (
        "Unconditional extractHandoffBrief call still present — the "
        "Ship button will reappear on reloaded shipped turns"
    )


# ── 2. Backend persistence chain ──────────────────────────────────────

async def _founder_token() -> str:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/auth/login", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
        })
        if r.status_code != 200:
            r = await c.post(f"{API}/auth/signup", json={
                "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
                "name": "Founder Test",
            })
        return r.json()["token"]


@pytest.mark.asyncio
async def test_turn_shipped_endpoint_persists_and_history_returns_it():
    """End-to-end: seed a session with one assistant turn, call
    /chat/turn/shipped, then GET /chat/history and confirm the
    shipped_task_id round-trips. This is the data path that
    suppresses the Ship button after reload."""
    from motor.motor_asyncio import AsyncIOMotorClient
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]
    token = await _founder_token()
    # Pull the founder's user_id so we can seed under their account.
    async with httpx.AsyncClient(timeout=10.0) as c:
        me = await c.get(f"{API}/usage/me",
                         headers={"Authorization": f"Bearer {token}"})
    user_id = me.json()["user_id"]

    session_id = f"shipnoreappear_{uuid.uuid4().hex[:8]}"
    fenced_assistant = (
        "Sure — here's the plan.\n\n"
        "```aurem-handoff\n"
        "In backend/routers/foo.py wire a /foo endpoint and add "
        "backend/tests/test_foo.py.\n"
        "```\n"
    )
    await db.chat_sessions.insert_one({
        "session_id": session_id, "user_id": user_id,
        "turns": [
            {"role": "user", "content": "wire foo", "ts": time.time()},
            {"role": "assistant", "content": fenced_assistant,
             "provider": "deepseek", "ts": time.time()},
        ],
        "updated_at": time.time(),
    })

    fake_task_id = f"task_{uuid.uuid4().hex[:10]}"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{API}/chat/turn/shipped",
            headers={"Authorization": f"Bearer {token}"},
            json={"session_id": session_id, "turn_index": 1,
                  "task_id": fake_task_id},
        )
    assert r.status_code == 200, r.text

    # History MUST return shipped_task_id on the assistant turn.
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{API}/chat/history?session_id={session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    turns = r.json()["messages"]
    assert len(turns) == 2
    assistant = turns[1]
    assert assistant["role"] == "assistant"
    assert assistant.get("shipped_task_id") == fake_task_id, (
        f"shipped_task_id not round-tripped via /chat/history: {assistant!r}"
    )

    # Cleanup.
    await db.chat_sessions.delete_one({"session_id": session_id})


@pytest.mark.asyncio
async def test_turn_shipped_off_by_one_falls_back_to_last_assistant():
    """If the frontend sends a stale turn_index (e.g. user double-clicked
    while a new turn streamed), the backend must still record the
    shipped state on the latest assistant turn — never silently no-op."""
    from motor.motor_asyncio import AsyncIOMotorClient
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]
    token = await _founder_token()
    async with httpx.AsyncClient(timeout=10.0) as c:
        me = await c.get(f"{API}/usage/me",
                         headers={"Authorization": f"Bearer {token}"})
    user_id = me.json()["user_id"]

    session_id = f"shipfallback_{uuid.uuid4().hex[:8]}"
    await db.chat_sessions.insert_one({
        "session_id": session_id, "user_id": user_id,
        "turns": [
            {"role": "user",      "content": "go", "ts": time.time()},
            {"role": "assistant", "content": "ok",
             "provider": "deepseek", "ts": time.time()},
        ],
        "updated_at": time.time(),
    })

    fake_task_id = f"task_{uuid.uuid4().hex[:10]}"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{API}/chat/turn/shipped",
            headers={"Authorization": f"Bearer {token}"},
            json={"session_id": session_id, "turn_index": 99,
                  "task_id": fake_task_id},   # deliberately out-of-range
        )
    assert r.status_code == 200, r.text
    # Fallback wrote to the actual last assistant turn (index 1).
    assert r.json()["turn_index"] == 1

    doc = await db.chat_sessions.find_one({"session_id": session_id})
    assert doc["turns"][1]["shipped_task_id"] == fake_task_id

    await db.chat_sessions.delete_one({"session_id": session_id})
