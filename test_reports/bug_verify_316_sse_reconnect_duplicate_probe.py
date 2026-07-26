"""Supplementary probe: after replaying the disconnect gap, check whether
the live queue emits the same gap events again. This is not the primary 315
contract (gap replay), but it is a directly related reconnect edge case.
"""
from __future__ import annotations

import asyncio

from bug_verify_316_sse_reconnect_core_probe import (
    _DB,
    _emit_after_stream_is_waiting,
    _next_data_event,
    _request,
)
from routers import loop as loop_router
from services import loop_engine as eng
from services import sse_replay_buffer as replay_buffer


async def main() -> int:
    replay_buffer._reset_for_tests()
    db = _DB()
    loop_id = "probe-loop-gap-dupes"
    user_id = "probe-user"

    async def fake_current_dev(_authorization):
        return {"user_id": user_id, "email": "probe@example.invalid", "is_admin": True, "tier": "founder"}

    orig_current_dev = loop_router.current_dev
    orig_get_db = loop_router.get_db
    loop_router.current_dev = fake_current_dev
    loop_router.get_db = lambda: db
    engine = eng.LoopEngine(db=db, loop_id=loop_id, user_id=user_id, project_id=None, user_message="probe", bin_ctx=None)
    eng.register(engine)
    try:
        resp1 = await loop_router.loop_stream(loop_id, request=_request(), authorization="Bearer probe", last_event_id=None)
        ait1 = resp1.body_iterator.__aiter__()
        await asyncio.wait_for(ait1.__anext__(), timeout=1.0)  # retry preamble
        await _emit_after_stream_is_waiting(engine, ait1, "event-0")
        id1, _, _ = await _emit_after_stream_is_waiting(engine, ait1, "event-1")
        await ait1.aclose()

        for i in range(2, 6):
            await engine._emit(eng.LoopState.EXECUTING, "execute", message=f"event-{i}")

        resp2 = await loop_router.loop_stream(loop_id, request=_request(), authorization="Bearer probe", last_event_id=id1)
        ait2 = resp2.body_iterator.__aiter__()
        await asyncio.wait_for(ait2.__anext__(), timeout=1.0)  # retry preamble
        seen = []
        try:
            for _ in range(8):
                fid, ev, _ = await _next_data_event(ait2, timeout=1.0)
                seen.append((fid, ev.get("message")))
        except Exception:
            pass
        finally:
            await ait2.aclose()
        print({"seen": seen, "messages": [m for _, m in seen]})
        return 0
    finally:
        loop_router.current_dev = orig_current_dev
        loop_router.get_db = orig_get_db
        eng.deregister(loop_id)
        replay_buffer._reset_for_tests()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))