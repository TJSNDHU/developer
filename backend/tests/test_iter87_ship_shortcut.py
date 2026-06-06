"""
test_iter87_ship_shortcut.py — "ship"/"do it" shortcut.

The real production bug:
  • User asks ORA to wire something. ORA replies with a clean
    ```aurem-handoff fence. User says "ship".
  • Old behaviour: chat router treats "ship" as a fresh prompt, runs
    the whole orchestrator + tool loop, hits 90 s wall, returns
    "I cut myself off". Zero progress. User retries → same wall.
  • New behaviour (Iter 87): if the prior assistant turn already
    emitted a handoff fence AND the new user prompt is a short
    confirmation, queue the cto_task DIRECTLY from the prior brief.
    No second orchestrator run. No second tool budget. Instant.

This test locks:
  1. The confirmation phrases set (positives + negatives).
  2. The wiring is mounted BEFORE the normal orchestrator path so the
    shortcut can intercept.
  3. End-to-end SSE flow when the shortcut fires (real backend call):
    streams meta → tokens → done with `ship_shortcut: True`.
  4. Falls through to the normal orchestrator when no prior handoff
    exists in the session (i.e. the user just typed "ship" out of
    the blue).
"""
from __future__ import annotations

import json
import os
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


# ── 1. Confirmation classifier — pure function ────────────────────────

def test_looks_like_ship_confirmation_true_positives():
    from routers.chat import _looks_like_ship_confirmation
    for p in (
        "ship", "Ship", "ship it", "SHIP VIA CTO",
        "do it", "do it now", "go", "go ahead",
        "yes", "yep", "ok", "okay", "proceed",
        "ship please", "send it", "execute", "run it",
        "ship.", "ship!", "ship?",
    ):
        assert _looks_like_ship_confirmation(p), (
            f"should match ship confirmation: {p!r}"
        )


def test_looks_like_ship_confirmation_true_negatives():
    """Anything substantive must fall through to the normal
    orchestrator path — we never want to silently ship on prose."""
    from routers.chat import _looks_like_ship_confirmation
    for p in (
        "",
        "ship a feature that adds dark mode",
        "should I add caching?",
        "go and check the file",
        "yes, but first read the readme",
        "ok, but only the backend",
        "do it without any tests",
        # Long prompts must always be ignored.
        "ship the whole pipeline including the docs and screenshots",
    ):
        assert not _looks_like_ship_confirmation(p), (
            f"should NOT match ship confirmation: {p!r}"
        )


# ── 2. Wiring lock — shortcut runs BEFORE orchestrator ────────────────

def test_chat_stream_invokes_shortcut_before_normal_path():
    src = _read("backend/routers/chat.py")
    # The shortcut helper must exist.
    assert "_maybe_ship_shortcut" in src
    # It must be invoked from chat_stream.
    assert "shipped_via_shortcut = await _maybe_ship_shortcut(" in src
    # AND the early-return must happen BEFORE the orchestrator path
    # (the orchestrator path is in the `async def gen()` block).
    shortcut_idx = src.index("shipped_via_shortcut = await")
    gen_idx     = src.index("async def gen()")
    assert shortcut_idx < gen_idx, (
        "Ship shortcut must be wired BEFORE the orchestrator gen() block."
    )
    # The handoff fence regex matches what MessageBubble.jsx looks for.
    assert "```aurem-handoff" in src


# ── 3. Real end-to-end via the live backend ───────────────────────────

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
        assert r.status_code == 200, r.text
        return r.json()["token"]


async def _seed_session_with_handoff(token: str, user_id: str,
                                      session_id: str, brief: str):
    """Insert a chat session whose last assistant turn contains a
    handoff fence — directly via Mongo so we don't have to drive the
    LLM to produce one in a test."""
    from motor.motor_asyncio import AsyncIOMotorClient
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]
    fenced = (
        "Sure — here's the plan.\n\n"
        f"```aurem-handoff\n{brief}\n```\n"
    )
    await db.chat_sessions.update_one(
        {"user_id": user_id, "session_id": session_id},
        {"$set": {
            "user_id":    user_id,
            "session_id": session_id,
            "messages": [
                {"role": "user",      "content": "wire the foo router",
                 "ts": time.time()},
                {"role": "assistant", "content": fenced,
                 "ts": time.time()},
            ],
            "updated_at": time.time(),
        }},
        upsert=True,
    )


def _parse_sse(raw: str) -> list[dict]:
    out: list[dict] = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            out.append(json.loads(line[5:].strip()))
        except Exception:
            continue
    return out


@pytest.mark.asyncio
async def test_ship_shortcut_streams_when_no_project_attached():
    """When the user has a prior handoff fence but no project_id, the
    shortcut still fires but degrades gracefully with a clear message."""
    token = await _founder_token()
    # Look up the founder's user_id so we can seed their session.
    async with httpx.AsyncClient(timeout=10.0) as c:
        me = await c.get(
            f"{API}/usage/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert me.status_code == 200, me.text
    user_id = me.json()["user_id"]
    session_id = f"shortcut_smoke_{uuid.uuid4().hex[:8]}"

    brief = (
        "In backend/routers/foo.py wire a /foo endpoint that returns "
        "{\"ok\": true}. Add backend/tests/test_foo.py covering the "
        "happy path. Mount the router in backend/main.py."
    )
    await _seed_session_with_handoff(token, user_id, session_id, brief)

    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(
            f"{API}/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt": "ship", "session_id": session_id,
                  "project_id": "home"},
        )
    assert r.status_code == 200, r.text
    frames = _parse_sse(r.text)
    # First frame must be a meta with ship_shortcut=true.
    meta = next((f for f in frames if f.get("meta")), None)
    assert meta, f"no meta frame in shortcut stream: {frames}"
    assert meta.get("ship_shortcut") is True
    assert meta.get("provider") == "aurem-ship-shortcut"
    # Final done frame.
    done = next((f for f in frames if f.get("done")), None)
    assert done, f"no done frame in shortcut stream: {frames}"
    assert done.get("provider") == "aurem-ship-shortcut"
    # Streamed tokens must explain the no-project state to the user.
    tokens = "".join(f.get("token", "") for f in frames if "token" in f)
    assert "no project" in tokens.lower() or "select" in tokens.lower(), (
        f"shortcut-no-project message missing: {tokens!r}"
    )


@pytest.mark.asyncio
async def test_ship_shortcut_falls_through_when_no_prior_handoff():
    """A bare 'ship' with no prior handoff in the session must fall
    through to the normal orchestrator path — we never want to
    silently no-op on a genuine 'ship' that has no context."""
    token = await _founder_token()
    session_id = f"shortcut_fallthrough_{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(
            f"{API}/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt": "ship", "session_id": session_id,
                  "project_id": "home"},
        )
    assert r.status_code == 200, r.text
    frames = _parse_sse(r.text)
    # The done frame must NOT be tagged ship_shortcut — it should look
    # like a normal orchestrator turn (provider != aurem-ship-shortcut).
    done = next((f for f in frames if f.get("done")), None)
    assert done, f"no done frame: {frames}"
    assert done.get("provider") != "aurem-ship-shortcut", (
        "shortcut fired without a prior handoff fence — should have "
        "fallen through to the orchestrator"
    )
