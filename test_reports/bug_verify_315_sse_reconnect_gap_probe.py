"""
Focused probe for Iter 315 bug verification: SSE reconnect gap replay.

This simulates the user-reported failure mode without a 20+ minute wait:
1. A client connects to /loop/{id}/stream and receives events seq 0..1.
2. The client disconnects mid-loop (closing the StreamingResponse iterator).
3. The engine continues to emit events 2..5 while the client is gone.
4. The client reconnects with Last-Event-ID: loop:1.

Expected fix contract: reconnect replay returns events 2,3,4,5 with no dupes.
Observed failure if only Mongo last_event is used after disconnect: only latest
gap event is returned, because intermediate last_event values were overwritten.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from starlette.requests import Request

from routers import loop as loop_router
from services import loop_engine as eng
from services import sse_replay_buffer as replay_buffer


class _Result:
    matched_count = 1
    modified_count = 1
    upserted_id = None


class _LoopSessions:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def update_one(self, query, update, upsert=False):
        loop_id = query["loop_id"]
        doc = self.docs.setdefault(loop_id, {"loop_id": loop_id})
        if "$set" in update:
            doc.update(update["$set"])
        else:
            doc.update(update)
        if "$setOnInsert" in update:
            for k, v in update["$setOnInsert"].items():
                doc.setdefault(k, v)
        return _Result()

    async def find_one(self, query, projection=None, sort=None):
        doc = self.docs.get(query.get("loop_id"))
        if not doc:
            return None
        return dict(doc)


class _DB:
    def __init__(self) -> None:
        self.loop_sessions = _LoopSessions()


def _request(query_string: bytes = b"") -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "headers": [],
        "path": "/loop/probe/stream",
        "query_string": query_string,
        "root_path": "",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 123),
    })


def _parse_event_frame(frame: str) -> tuple[str | None, dict | None]:
    frame_id = None
    data_lines = []
    for line in frame.split("\n"):
        if line.startswith("id:"):
            frame_id = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
    if not data_lines:
        return frame_id, None
    return frame_id, json.loads("\n".join(data_lines))


async def _next_data_event(ait, timeout=3.5):
    while True:
        frame = await asyncio.wait_for(ait.__anext__(), timeout=timeout)
        if isinstance(frame, bytes):
            frame = frame.decode()
        fid, ev = _parse_event_frame(frame)
        if ev is not None:
            return fid, ev, frame


async def main() -> int:
    replay_buffer._reset_for_tests()
    db = _DB()
    loop_id = "probe-loop-gap"
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
        # Initial stream receives two events, causing the stream layer to assign ids 0 and 1.
        resp1 = await loop_router.loop_stream(loop_id, request=_request(), authorization="Bearer probe", last_event_id=None)
        ait1 = resp1.body_iterator.__aiter__()
        retry_frame = await asyncio.wait_for(ait1.__anext__(), timeout=1.0)
        if isinstance(retry_frame, bytes):
            retry_frame = retry_frame.decode()

        await engine._emit(eng.LoopState.EXECUTING, "execute", message="event-0")
        id0, ev0, _ = await _next_data_event(ait1)
        await engine._emit(eng.LoopState.EXECUTING, "execute", message="event-1")
        id1, ev1, _ = await _next_data_event(ait1)

        # Simulate browser/network disconnect.
        await ait1.aclose()
        live_after_close = eng.lookup(loop_id) is not None

        # Engine continues emitting during the disconnect gap.
        for i in range(2, 6):
            await engine._emit(eng.LoopState.EXECUTING, "execute", message=f"event-{i}")

        buffered_before_reconnect = replay_buffer.buffer_stats().get(loop_id, {}).get("buffered")
        queued_gap_events = engine.queue.qsize()

        # Reconnect with Last-Event-ID from the last event actually received by the client.
        resp2 = await loop_router.loop_stream(loop_id, request=_request(), authorization="Bearer probe", last_event_id=id1)
        ait2 = resp2.body_iterator.__aiter__()
        retry2 = await asyncio.wait_for(ait2.__anext__(), timeout=1.0)
        if isinstance(retry2, bytes):
            retry2 = retry2.decode()

        replayed = []
        try:
            # Collect a bounded number of reconnect frames. In the broken path this
            # yields only one data event (the current Mongo last_event) followed by
            # keepalives, so waiting only for data events would never finish.
            for _ in range(6):
                frame = await asyncio.wait_for(ait2.__anext__(), timeout=3.5)
                if isinstance(frame, bytes):
                    frame = frame.decode()
                fid, ev = _parse_event_frame(frame)
                if ev is None:
                    continue
                replayed.append({"id": fid, "message": ev.get("message"), "state": ev.get("state")})
                if len(replayed) >= 4:
                    break
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        finally:
            await ait2.aclose()

        expected_messages = [f"event-{i}" for i in range(2, 6)]
        actual_messages = [r["message"] for r in replayed]
        result = {
            "retry_preamble_ok": str(retry_frame).strip() == "retry: 3000",
            "initial_ids": [id0, id1],
            "initial_messages": [ev0.get("message"), ev1.get("message")],
            "engine_still_registered_after_disconnect": live_after_close,
            "buffered_events_before_reconnect": buffered_before_reconnect,
            "engine_queue_gap_events_available_but_detached": queued_gap_events,
            "reconnect_last_event_id": id1,
            "expected_gap_messages": expected_messages,
            "actual_replayed_messages": actual_messages,
            "passed": actual_messages == expected_messages,
        }
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["passed"] else 1
    finally:
        loop_router.current_dev = orig_current_dev
        loop_router.get_db = orig_get_db
        eng.deregister(loop_id)
        replay_buffer._reset_for_tests()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))