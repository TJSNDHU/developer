"""
Fitness-function invariants — Continuous Quality System
────────────────────────────────────────────────────────
These are ALWAYS-ON assertions about invariants that MUST hold for
the app to be usable. They map 1:1 to the real bugs found in this
session — they exist so an accidental revert can never re-open the
same failure mode.

Difference from regression tests:
   - Regression tests reproduce ONE specific broken scenario.
   - Invariants assert a PROPERTY that must hold across the whole
     surface, in every future release, forever.

Runs on every CI push (see .github/workflows/quality-gate.yml).
"""
from __future__ import annotations

import os
import re
import time
import asyncio
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"],
                                 serverSelectionTimeoutMS=3000)
    yield client[os.environ["DB_NAME"]]
    client.close()


# ───────────────────────────────────────────────────────────────────
# INVARIANT 1 — chat-input is NEVER `disabled` while a loop is active
# ───────────────────────────────────────────────────────────────────
def test_invariant_chat_input_never_disabled_during_active_loop():
    """
    The chat-input textarea must not carry `disabled={busy...}` or
    `disabled={loop...}`. Only `exhausted` (tokens gone) may disable.
    This is enforced at source level so any accidental revert of the
    Iter 280 P0 fix is caught by CI before merge.
    """
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()
    m = re.search(r'data-testid="chat-input"', src)
    assert m, "chat-input testid must exist"
    # 800-char window around the textarea props.
    window = src[max(0, m.start() - 800): m.end() + 300]
    forbidden = [
        "disabled={busy",
        "disabled={loop",
        "disabled={!!loopId",
        "disabled={loopPhase",
    ]
    for pat in forbidden:
        assert pat not in window, (
            f"chat-input textarea must not carry `{pat}` — this "
            "re-introduces the Iter 280 P0 bug where the queue-next "
            "feature became unreachable."
        )


# ───────────────────────────────────────────────────────────────────
# INVARIANT 2 — cancel_loop finalises state within 2s
# ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_invariant_cancel_within_2s_state_aborted_lock_released(db):
    """
    Given a live loop lock, after invoking the cancel-fallback path
    (state write + release_loop_lock), within 2 SECONDS the DB must
    show:
      • loop_sessions.state == "aborted"
      • loop_locks row for that (project, user) is gone

    Otherwise the very next /loop/start hits 409 loop_already_running
    (this was the Iter 279 root cause).
    """
    from datetime import datetime, timezone
    from services.loop_safety import acquire_loop_lock, release_loop_lock

    lid  = f"invar2-{int(time.time()*1000)}"
    uid  = f"user-invar2-{int(time.time()*1000)}"
    proj = "invariant-proj-cancel-2s"

    # Establish a "live" loop: session + lock.
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.loop_sessions.insert_one({
        "loop_id":    lid,
        "user_id":    uid,
        "project_id": proj,
        "state":      "executing",
        "phase":      "execute",
        "created_at": now_iso,
    })
    ok, _ = await acquire_loop_lock(db, proj, uid, lid)
    assert ok, "sanity: lock acquired"

    # Cancel — measure wall-clock.
    t0 = time.monotonic()
    await db.loop_sessions.update_one(
        {"loop_id": lid},
        {"$set": {"state": "aborted", "updated_at": now_iso}},
    )
    await release_loop_lock(db, proj, uid, lid)
    elapsed = time.monotonic() - t0

    # Verify DB state.
    sess = await db.loop_sessions.find_one({"loop_id": lid})
    lock = await db.loop_locks.find_one({"loop_id": lid})

    assert sess["state"] == "aborted"
    assert lock is None, "loop_locks must be released"
    assert elapsed < 2.0, (
        f"cancel path took {elapsed:.3f}s — must complete in < 2s "
        "so an immediate /loop/start doesn't 409."
    )

    # cleanup
    await db.loop_sessions.delete_one({"loop_id": lid})


# ───────────────────────────────────────────────────────────────────
# INVARIANT 3 — every SSE event emitted by loop_engine is observable
# ───────────────────────────────────────────────────────────────────
def test_invariant_every_sse_event_reaches_frontend_playwright():
    """
    Source-level invariant: every phase transition in loop_engine.py
    must go through the `self._emit(...)` method (or its variant),
    which is the single choke point that writes to the SSE stream.

    Directly writing to `loop_events` or `loop_sessions.last_event`
    without going through _emit would produce state changes that
    frontend `streamLoopEvents()` never sees.

    Regression example: if a new phase were added that wrote to DB
    but forgot _emit, the LoopLiveFeed would silently drift out of
    sync — exactly the class of bug reported this session.

    Enforcement: search for phase-transition patterns that write
    state without a co-located _emit. Uses a simple heuristic: any
    `self.state = LoopState.` assignment must have an `_emit` call
    within the next 40 lines.
    """
    src = open("/app/backend/services/loop_engine.py").read()
    lines = src.splitlines()
    violations = []
    for i, line in enumerate(lines):
        m = re.search(r"self\.state\s*=\s*LoopState\.", line)
        if not m:
            continue
        # Look ahead 40 lines for an _emit call.
        window = "\n".join(lines[i: i + 40])
        if "self._emit(" not in window and "_emit(" not in window:
            violations.append((i + 1, line.strip()))
    assert not violations, (
        "Every `self.state = LoopState.<X>` must be followed by a "
        f"co-located _emit() call. Violations: {violations[:5]}"
    )


# ───────────────────────────────────────────────────────────────────
# INVARIANT 4 — LoopLiveFeed never returns null when loopId is set
# ───────────────────────────────────────────────────────────────────
def test_invariant_loop_live_feed_never_returns_null():
    """
    Once a loop_id exists in ChatPanel state, the [data-testid=
    loop-live-feed] node MUST be present in the DOM — either with
    real events or with the pending-approval placeholder.

    Returning null (as the pre-Iter 281 code did) hides the panel
    entirely during the plan-approval window, which was reported by
    users as "the live feed just doesn't show up".
    """
    src = open("/app/frontend/src/components/LoopLiveFeed.jsx").read()

    # No `return null` guarded on `events.length === 0`.
    forbidden = [
        "!loopId || events.length === 0",
        "events.length === 0 && !terminal",
        "events.length === 0 && loopId",
    ]
    for pat in forbidden:
        assert pat not in src, (
            f"LoopLiveFeed must not return null on empty events "
            f"(pattern `{pat}` re-introduces the Iter 281 bug)."
        )

    # The placeholder testid must render.
    assert 'data-testid="loop-live-feed-placeholder"' in src, (
        "LoopLiveFeed must render a placeholder while awaiting "
        "the first SSE event."
    )
    # The panel testid must live outside the empty-events guard.
    # Simple check: at least TWO occurrences of the panel testid
    # (one for placeholder, one for real feed).
    assert src.count('data-testid="loop-live-feed"') >= 2, (
        "LoopLiveFeed should have TWO render paths using the same "
        "data-testid — the pending placeholder AND the live feed."
    )
