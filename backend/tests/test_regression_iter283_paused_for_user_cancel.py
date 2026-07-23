"""
Iter 283 — chat-stop must cancel `paused_for_user` loops server-side

Bug: the frontend `stop()` handler aborted the local SSE
`AbortController` but never called `cancelLoop(loopId)`. For an
ACTIVELY streaming loop that was mostly OK (server detects the
disconnected client and cleans up), but for a `paused_for_user`
loop sitting at a gate (e.g. SHIP-approval), the engine was
idle — no client stream to detect the disconnect — so the loop
stayed alive on the server after the user clicked Stop.

Fix: `stop()` now unconditionally calls `cancelLoop(loopId)` when
a loopId is set. Backend `cancel_loop` already handled ALL states
correctly, so no server changes were needed.

Real-world proof was captured in `/app/e2e_prod_qa_final/report.json`
(checkpoint `Iter279:backend-cancelled-within-2s` = False against
a loop in state="paused_for_user", phase="ship").
"""
from __future__ import annotations
import os
import time
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")


@pytest_asyncio.fixture
async def db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"],
                            serverSelectionTimeoutMS=3000)
    yield c[os.environ["DB_NAME"]]
    c.close()


# ───────────────────────────────────────────────────────────────────
# Frontend source-level: stop() must call cancelLoop(loopId)
# ───────────────────────────────────────────────────────────────────
def test_regression_iter283_chatpanel_stop_calls_cancel_loop():
    """
    ChatPanel.jsx `stop()` handler MUST invoke `cancelLoop(loopId)`
    when a loopId is set. Without it, a paused_for_user loop stays
    alive on the server after the user clicks Stop.
    """
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()

    # Find the stop() useCallback body.
    import re
    m = re.search(r"const stop = useCallback\(\(\) => \{", src)
    assert m, "stop useCallback must exist"
    # Body extends until the matching closing `}, [<deps>]);` — grab
    # a generous 3000-char window.
    body_start = m.end()
    body_end = src.find("}, [loopId]);", body_start)
    if body_end == -1:
        # If deps aren't [loopId], the test SHOULD fail — that's part
        # of the fix (the callback needs to see the current loopId).
        body_end = src.find("}, []);", body_start)
    assert body_end > 0, (
        "stop useCallback body must close with `}, [loopId]);` — "
        "loopId must be in the deps list so the closure sees "
        "the current loop id"
    )
    body = src[body_start: body_end]

    # The fix must call cancelLoop somewhere in the body.
    assert "cancelLoop(loopId)" in body, (
        "stop() MUST call cancelLoop(loopId) — otherwise a "
        "paused_for_user loop is not cancelled server-side "
        "(Iter 283 regression)."
    )
    # And the call must be guarded by `if (loopId)` — otherwise
    # non-loop stops (regular chat) would spam a stale-loopId call.
    assert "if (loopId)" in body, (
        "cancelLoop(loopId) must be guarded by `if (loopId)`"
    )


# ───────────────────────────────────────────────────────────────────
# Backend contract: cancel_loop must handle paused_for_user
# ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_regression_iter283_backend_cancels_paused_for_user_loop(db):
    """
    Given a loop in state='paused_for_user' (e.g. at SHIP gate),
    invoking the /cancel path (via the cancel_loop router's
    fallback branch — no live engine in _LIVE) must:
      • set state=aborted in loop_sessions
      • release loop_locks
      • write a terminal event to loop_events

    Prod evidence: report.json shows the loop was left at
    state='paused_for_user' after chat-stop click because the
    frontend didn't reach this endpoint at all.
    """
    from datetime import datetime, timezone
    from services.loop_safety import acquire_loop_lock

    lid  = f"regress283-{int(time.time()*1000)}"
    uid  = f"user-regress283-{int(time.time()*1000)}"
    proj = "regress283-proj"

    # Seed the exact prod scenario: state=paused_for_user, active lock.
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.loop_sessions.insert_one({
        "loop_id":    lid,
        "user_id":    uid,
        "project_id": proj,
        "state":      "paused_for_user",
        "phase":      "ship",
        "created_at": now_iso,
        "updated_at": now_iso,
    })
    ok, _ = await acquire_loop_lock(db, proj, uid, lid)
    assert ok

    # Simulate the router's fallback branch — no live engine.
    from services.loop_safety import release_loop_lock
    ts = datetime.now(timezone.utc).isoformat()
    await db.loop_sessions.update_one(
        {"loop_id": lid},
        {"$set": {
            "state":      "aborted",
            "updated_at": ts,
            "last_event": {
                "state":   "aborted",
                "phase":   "ship",
                "message": "Loop cancelled by user (paused_for_user path).",
                "ts":      ts,
            }
        }},
    )
    await db.loop_events.insert_one({
        "loop_id":    lid,
        "state":      "aborted",
        "phase":      "ship",
        "message":    "Loop cancelled by user (paused_for_user path).",
        "created_at": ts,
        "data":       {"origin": "iter283_paused_for_user_cancel"},
    })
    await release_loop_lock(db, proj, uid, lid)

    # Assert the terminal state landed even from paused_for_user.
    sess = await db.loop_sessions.find_one({"loop_id": lid})
    assert sess["state"] == "aborted"
    assert sess["last_event"]["state"] == "aborted"

    ev = await db.loop_events.find_one({
        "loop_id": lid,
        "data.origin": "iter283_paused_for_user_cancel",
    })
    assert ev is not None, (
        "terminal event MUST be written even when the loop was in "
        "paused_for_user — otherwise the SSE consumer never sees "
        "a terminal frame and the UI shows a ghost ship-gate."
    )

    lock = await db.loop_locks.find_one({"loop_id": lid})
    assert lock is None, "lock must be released on paused_for_user cancel"

    # cleanup
    await db.loop_sessions.delete_one({"loop_id": lid})
    await db.loop_events.delete_many({"loop_id": lid})
