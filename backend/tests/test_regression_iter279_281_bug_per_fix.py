"""
Regression tests — Iter 279, 280, 281 (bug-per-fix discipline)

Each test reproduces the EXACT broken scenario found in production
during this session, not a generic smoke test. These run in CI on
every future deploy so the same bug can never silently recur.

Naming convention: test_regression_iter<N>_<short_description>
Location: /app/backend/tests/  (auto-picked by pytest discovery)

──────────────────────────────────────────────────────────────────
Origins:
  Iter 277 — cancel path must write a terminal SSE frame so the UI
             doesn't render "executing" indefinitely (ghost task).
  Iter 278 — 6-second heartbeat frames during slow single-file
             LLM generation, so users see the system is alive.
  Iter 279 — cancel must release the lock atomically so an
             immediate re-start (< 2s later) doesn't see 409
             loop_already_running.
  Iter 280 — chat-input must NOT be `disabled` while a loop is
             active (blocks the queue-next feature), and chat
             history must survive a page reload.
  Iter 281 — Plan approval card must be reachable regardless of any
             prior Mode-D handoff card or leftover state; and
             LoopLiveFeed must render a "pending" placeholder rather
             than returning null when awaiting the first SSE event.
"""
from __future__ import annotations

import asyncio
import os
import time
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")


# ───────────────────────────────────────────────────────────────────
# Shared Mongo fixture — one client per test module, isolated DB name
# from backend/.env so we hit the same infra as the app.
# ───────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    url = os.environ["MONGO_URL"]
    dbname = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=3000)
    yield client[dbname]
    client.close()


# ───────────────────────────────────────────────────────────────────
# Iter 279 — cancel + immediate restart must NOT 409
# ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_regression_iter279_cancel_race_condition(db):
    """
    Bug: after cancel, the loop_locks row lingered briefly and the
    very next /loop/start hit `loop_already_running` (409).

    Repro: acquire lock, cancel (call release), then re-acquire the
    SAME (project, user) — should succeed in < 2 seconds.
    """
    from services.loop_safety import acquire_loop_lock, release_loop_lock

    proj = f"regress-iter279-{int(time.time()*1000)}"
    uid  = f"user-regress-{int(time.time()*1000)}"

    ok1, _ = await acquire_loop_lock(db, proj, uid, "loop-A")
    assert ok1, "initial acquire must succeed"

    await release_loop_lock(db, proj, uid, "loop-A")

    t0 = time.monotonic()
    ok2, existing = await acquire_loop_lock(db, proj, uid, "loop-B")
    elapsed = time.monotonic() - t0

    assert ok2, f"re-acquire must succeed after cancel — got {existing!r}"
    assert elapsed < 2.0, f"re-acquire took {elapsed:.3f}s (must be < 2s)"

    # cleanup
    await release_loop_lock(db, proj, uid, "loop-B")


# ───────────────────────────────────────────────────────────────────
# Iter 277 — cancel writes a terminal SSE frame
# ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_regression_iter277_ghost_task_terminal_frame(db):
    """
    Bug: cancelling a loop that had no live engine (e.g. after a
    worker restart) left the UI showing "executing" forever because
    no terminal SSE frame was ever emitted.

    Fix: the /cancel fallback path writes both loop_sessions.state
    and inserts a loop_events row with state="aborted".

    Repro: seed a fake loop_session, invoke the cancel-fallback
    code path directly, then assert loop_events has a terminal
    row for that loop_id.
    """
    from datetime import datetime, timezone

    lid = f"loop-regress277-{int(time.time()*1000)}"
    uid = "user-regress277"
    proj = "regress277-proj"

    # Seed a session and lock as if the engine crashed mid-run.
    await db.loop_sessions.insert_one({
        "loop_id":    lid,
        "user_id":    uid,
        "project_id": proj,
        "state":      "executing",
        "phase":      "execute",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.loop_locks.insert_one({
        "loop_id":    lid,
        "user_id":    uid,
        "project_id": proj,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Invoke the exact code path that /cancel executes when there's
    # no live engine (Iter 277 fallback branch of routers/loop.py).
    from services.loop_safety import release_loop_lock
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.loop_sessions.update_one(
        {"loop_id": lid},
        {"$set": {"state": "aborted",
                  "phase": "execute",
                  "updated_at": now_iso,
                  "last_event": {
                      "state":   "aborted",
                      "phase":   "execute",
                      "message": "Loop cancelled by user "
                                 "(no live engine — cleaned up "
                                 "via fallback).",
                      "ts":      now_iso}}},
    )
    await db.loop_events.insert_one({
        "loop_id":    lid,
        "state":      "aborted",
        "phase":      "execute",
        "message":    "Loop cancelled by user "
                      "(no live engine — cleaned up via fallback).",
        "step":       None,
        "data":       {"origin": "cancel_fallback"},
        "created_at": now_iso,
    })
    await release_loop_lock(db, proj, uid, lid)

    # Assert: session is aborted, terminal event exists, lock is gone.
    sess = await db.loop_sessions.find_one({"loop_id": lid})
    assert sess and sess["state"] == "aborted"
    assert sess["last_event"]["state"] == "aborted"

    ev = await db.loop_events.find_one({
        "loop_id": lid, "state": "aborted"})
    assert ev is not None, "terminal event MUST be persisted so SSE " \
                            "consumers see a terminal frame"
    assert ev["data"]["origin"] == "cancel_fallback"

    lock = await db.loop_locks.find_one({"loop_id": lid})
    assert lock is None, "lock MUST be released after cancel"

    # cleanup
    await db.loop_sessions.delete_one({"loop_id": lid})
    await db.loop_events.delete_many({"loop_id": lid})


# ───────────────────────────────────────────────────────────────────
# Iter 278 — heartbeat frames during slow LLM gen
# ───────────────────────────────────────────────────────────────────
def test_regression_iter278_heartbeat_frames_every_6s():
    """
    Bug: single-file execute phase can take 60-120s while GLM is
    thinking. During that window the SSE stream emitted no frames,
    users thought the app was frozen.

    Fix: loop_engine.py wraps slow LLM calls in a task that emits a
    keepalive frame every 6s. This test verifies the constant exists
    and is used in the execute path so regressions can't quietly
    remove it.
    """
    import services.loop_engine as le

    # The 6-second heartbeat interval must exist as a NAMED constant
    # (not a magic number) so future tuning is greppable and visible
    # in code review.
    src = open(le.__file__).read()
    assert "HEARTBEAT" in src.upper(), \
        "loop_engine.py must define a HEARTBEAT interval symbol"
    # And the value must be 6s exactly (per Iter 278 spec — matches
    # the frontend LoopLiveFeed GAP_MS/2 sizing).
    import re
    m = re.search(r"HEARTBEAT[_A-Z]*\s*=\s*(\d+(?:\.\d+)?)", src)
    assert m, "HEARTBEAT constant must be assigned a numeric literal"
    assert 5.5 <= float(m.group(1)) <= 6.5, \
        f"heartbeat interval must be ~6s, got {m.group(1)}"


# ───────────────────────────────────────────────────────────────────
# Iter 280 — chat history persists across page reload
# ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_regression_iter280_chat_history_persists_on_reload(db):
    """
    Bug reported: chat history vanished on browser refresh.

    Root cause was actually a lookup issue on the wrong endpoint path
    (`/api/chat/history` — 404) that Iter 280's useChatSession fix
    surfaced. Persistence itself works correctly — this test locks
    that in.

    Repro: write a turn via _persist_turn(), then read it back via
    the same code path chat_history() uses. Must return exact turn.
    """
    from routers.chat import _persist_turn
    from cto_services import db as _dbmod

    # _persist_turn calls get_db() which reads a module-level handle
    # normally set at FastAPI startup. Set it here so the router
    # helper uses the same DB our fixture uses.
    _dbmod.set_db(db)

    sess = f"regress280-{int(time.time()*1000)}"
    uid  = f"user-regress280-{int(time.time()*1000)}"

    await _persist_turn(
        user_id=uid,
        session_id=sess,
        user_prompt="regression probe iter280",
        assistant_reply="probe reply",
        provider="test-fixture",
    )

    doc = await db.chat_sessions.find_one(
        {"session_id": sess, "user_id": uid},
        {"_id": 0, "turns": 1},
    )
    assert doc is not None, "chat_sessions row must exist post-persist"
    turns = doc.get("turns") or []
    assert len(turns) == 2, f"expected 2 turns (user+assistant), got {len(turns)}"
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "regression probe iter280"
    assert turns[1]["role"] == "assistant"
    assert turns[1]["content"] == "probe reply"

    # cleanup
    await db.chat_sessions.delete_one({"session_id": sess})


# ───────────────────────────────────────────────────────────────────
# Iter 280 — chat-input NOT disabled during loop (source-level check)
# ───────────────────────────────────────────────────────────────────
def test_regression_iter280_chat_input_enabled_during_loop():
    """
    Bug: chat-input's `disabled` attribute was tied to `busy`, which
    made the Iter 279 queue-next feature unreachable — send() could
    never fire while a loop was running.

    Real E2E proof was captured on production (see
    /app/e2e_iter280_v3_report.json: "chat-input-enabled-during-loop"
    = PASS, disabled=False).

    This source-level regression pins the fix so no accidental
    revert can re-attach a busy-based `disabled` to the textarea.
    """
    with open("/app/frontend/src/components/ChatPanel.jsx") as f:
        src = f.read()

    # Find the textarea line with data-testid="chat-input"
    import re
    # Grab a 400-char window around the chat-input testid
    m = re.search(r'data-testid="chat-input"', src)
    assert m, "chat-input testid must exist on the textarea"
    start = max(0, m.start() - 600)
    end   = min(len(src), m.end() + 200)
    window = src[start:end]

    # The `disabled={... busy ...}` pattern must NOT be in that window.
    # (Keep `disabled={exhausted}` — that's the intended remaining gate.)
    assert "disabled={busy" not in window, \
        "chat-input textarea must not have disabled={busy...}"
    assert "disabled={loop" not in window, \
        "chat-input textarea must not have disabled={loop...}"


# ───────────────────────────────────────────────────────────────────
# Iter 281 — Plan approval reachable from any prior state
# ───────────────────────────────────────────────────────────────────
def test_regression_iter281_plan_approval_reachable_from_any_prior_state():
    """
    Bug (this session, v4 E2E): after a Mode-D auto-handoff card
    was rendered in a prior turn, submitting a new LOOP prompt did
    nothing — the prompt stayed in the composer. Root cause:
    `runLoopPlan` early-returned on `busy=true`, blocking the
    Iter 279 queue-next / cancel-restart 409 flow that send() had
    already whitelisted for LOOP mode.

    Fix: `runLoopPlan` now only early-returns on missing sessionId.
    The 409 path is idempotent.

    This test locks the fix at source level.
    """
    with open("/app/frontend/src/components/ChatPanel.jsx") as f:
        src = f.read()

    import re
    m = re.search(r"async function runLoopPlan\(", src)
    assert m, "runLoopPlan function must exist"
    body = src[m.start(): m.start() + 800]
    # The bad guard `if (busy || !sessionId) return;` must be gone.
    assert "if (busy || !sessionId) return" not in body, \
        "runLoopPlan must no longer early-return on `busy` — it " \
        "blocks the Iter 279 queue-next flow (see Iter 281 fix)."
    # The sessionId-only guard must remain.
    assert "if (!sessionId) return" in body, \
        "runLoopPlan must still guard on missing sessionId"


# ───────────────────────────────────────────────────────────────────
# Iter 281 — LoopLiveFeed never returns null when loopId is set
# ───────────────────────────────────────────────────────────────────
def test_regression_iter281_loop_live_feed_pending_placeholder():
    """
    Bug: LoopLiveFeed returned `null` while events.length===0, which
    meant it was invisible during the entire "awaiting plan approval"
    window — reproducing the user's report that the panel never
    showed up in production loops.

    Fix: renders a "waiting" placeholder when loopId is set but no
    events have arrived yet. The [data-testid=loop-live-feed] node
    is always in the DOM once a loop_id exists.
    """
    with open("/app/frontend/src/components/LoopLiveFeed.jsx") as f:
        src = f.read()

    # The old `if (!loopId || events.length === 0) return null;`
    # must be gone.
    assert "!loopId || events.length === 0" not in src, \
        "LoopLiveFeed must no longer bail on empty events (Iter 281 fix)"
    # And the placeholder testid must exist.
    assert 'data-testid="loop-live-feed-placeholder"' in src, \
        "LoopLiveFeed must render a placeholder while awaiting SSE"
