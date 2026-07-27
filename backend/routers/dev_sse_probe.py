"""
routers/dev_sse_probe.py — Iter 309 · SSE 25-min reconnect validation harness

TEST-ONLY endpoint. Emits synthetic heartbeat events on `/loop/{id}/stream`
semantics (same replay buffer, same STREAM_MAX_S cap, same Last-Event-ID
resume contract) so the harness in `scripts/iter309_sse_reconnect_harness.py`
can stress the reconnect path for 25+ minutes without needing a genuine
long-running loop.

Deliberately isolated in its own router so removing the whole file after
validation is a one-line change to `server.py`. Guarded by an env var:
`AUREM_ENABLE_SSE_PROBE=1` must be present at server start, otherwise
this router refuses every request with a 404 — safe to leave the router
included in production as long as the env var is off.

Endpoints (all admin-scoped, all under /api/aurem-dev):
  • GET  /_iter309_probe/start        — start a synthetic 30-min loop
  • GET  /_iter309_probe/{id}/stream  — SSE stream that emits a
        heartbeat every 15 s using the loop_engine's real _emit path
        (so the same replay buffer + cap semantics fire naturally).
  • POST /_iter309_probe/{id}/stop    — mark the synthetic loop DONE.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from services import sse_replay_buffer as _sse_buf
from cto_services.auth import current_dev

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/aurem-dev/_iter309_probe", tags=["iter309-probe"])


# ── Gate: refuse to run in production unless explicitly enabled ────────
def _probe_enabled() -> bool:
    """Test-only guard. `AUREM_ENABLE_SSE_PROBE=1` must be set at server
    start. In prod we ship the router but the env var is off, so every
    request 404s — safe by default."""
    return os.environ.get("AUREM_ENABLE_SSE_PROBE") == "1"


# ── In-memory synthetic-loop registry ──────────────────────────────────
# Each entry: {"user_id": str, "started_at": float, "seq": int,
#              "stop": asyncio.Event, "done": bool}
_PROBES: dict[str, dict] = {}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@router.get("/start")
async def probe_start(authorization: Optional[str] = Header(None)):
    """Create a synthetic probe loop. Returns `{"loop_id": ...}`.
    The caller then opens `/stream` and holds it — the endpoint will
    emit heartbeats until either the caller aborts or 30 minutes pass.
    """
    if not _probe_enabled():
        raise HTTPException(404, "probe disabled")
    user = await current_dev(authorization)
    loop_id = f"iter309probe_{uuid.uuid4().hex[:12]}"
    _PROBES[loop_id] = {
        "user_id":    user["user_id"],
        "started_at": time.time(),
        "seq":        0,
        "stop":       asyncio.Event(),
        "done":       False,
    }
    logger.info("[iter309-probe] created synthetic loop %s for user %s",
                loop_id, user["user_id"])
    return {
        "loop_id":    loop_id,
        "started_at": _iso_now(),
        "duration_s": 30 * 60,
        "note":       (
            "test-only synthetic loop. GET /stream to attach; "
            "POST /stop to end early. Auto-terminates at 30 min."
        ),
    }


@router.post("/{loop_id}/stop")
async def probe_stop(loop_id: str,
                     authorization: Optional[str] = Header(None)):
    if not _probe_enabled():
        raise HTTPException(404, "probe disabled")
    user = await current_dev(authorization)
    p = _PROBES.get(loop_id)
    if not p:
        raise HTTPException(404, "probe not found")
    if p["user_id"] != user["user_id"]:
        raise HTTPException(403, "not your probe")
    p["stop"].set()
    p["done"] = True
    return {"ok": True, "seq_at_stop": p["seq"]}


@router.get("/{loop_id}/stream")
async def probe_stream(loop_id: str,
                       request: Request,
                       authorization: Optional[str] = Header(None),
                       last_event_id: Optional[str] = Header(
                           None, alias="Last-Event-ID",
                       )):
    """SSE stream. Emits a synthetic heartbeat every 15 s using the
    SAME replay buffer as the real loop stream, so the harness sees
    the same cap-and-reconnect behaviour production traffic gets.

    On reconnect, the client sends `Last-Event-ID: <loop_id>:<seq>`
    and the buffer replays everything since. The natural
    `STREAM_MAX_S` cap (20 min in this build) fires as usual; the
    caller reconnects and validates event continuity.
    """
    if not _probe_enabled():
        raise HTTPException(404, "probe disabled")
    user = await current_dev(authorization)
    p = _PROBES.get(loop_id)
    if not p:
        raise HTTPException(404, "probe not found")
    if p["user_id"] != user["user_id"]:
        raise HTTPException(403, "not your probe")

    # Import loop router's STREAM_MAX_S so we cap on the SAME clock the
    # production stream uses (currently 20 min).
    from routers.loop import STREAM_MAX_S

    lei_hdr = last_event_id or request.query_params.get(
        "last_event_id",
    ) or ""
    replay_after_seq = _sse_buf.parse_last_event_id(lei_hdr, loop_id)

    async def gen():
        stream_started = time.monotonic()
        yield f"retry: {_sse_buf.BROWSER_RECONNECT_MS}\n\n"

        # ── Replay buffered events since Last-Event-ID ─────────────
        for _seq, _ev in _sse_buf.replay_after(loop_id, replay_after_seq):
            _ev_id = f"{loop_id}:{_seq}"
            yield f"id: {_ev_id}\ndata: {json.dumps(_ev)}\n\n"

        # ── Live heartbeat loop ────────────────────────────────────
        HEARTBEAT_S = 15
        while True:
            # Natural STREAM_MAX_S cap (20 min) — exactly the
            # behaviour we want the harness to stress.
            if time.monotonic() - stream_started > STREAM_MAX_S:
                yield "data: " + json.dumps({
                    "state":   "stream_capped",
                    "phase":   "probe",
                    "message": (
                        "SSE probe stream capped — reconnect via "
                        "Last-Event-ID to resume."
                    ),
                    "ts":      time.time(),
                }) + "\n\n"
                return

            # 30-min probe auto-termination.
            if time.time() - p["started_at"] > 30 * 60:
                p["done"] = True

            if p["done"]:
                p["seq"] += 1
                ev = {
                    "state":       "completed",
                    "phase":       "probe",
                    "message":     "iter309-probe: terminal",
                    "step":        5,
                    "total_steps": 5,
                    "ts":          time.time(),
                    "probe_seq":   p["seq"],
                }
                _s, _ev_id = _sse_buf.record(loop_id, ev)
                yield f"id: {_ev_id}\ndata: {json.dumps(ev)}\n\n"
                return

            # Wait either for the stop signal or the heartbeat tick.
            try:
                await asyncio.wait_for(p["stop"].wait(), HEARTBEAT_S)
                # stop was signalled — one more iteration will emit
                # the terminal frame and return.
                continue
            except asyncio.TimeoutError:
                pass

            p["seq"] += 1
            ev = {
                "state":       "probe_running",
                "phase":       "probe",
                "message":     f"iter309-probe heartbeat #{p['seq']}",
                "step":        1,
                "total_steps": 5,
                "ts":          time.time(),
                "probe_seq":   p["seq"],
                # Include a monotonic timestamp so the harness can
                # measure real gap between successive heartbeats even
                # across a wall-clock jump.
                "monotonic":   round(time.monotonic() - stream_started, 3),
            }
            _s, _ev_id = _sse_buf.record(loop_id, ev)
            yield f"id: {_ev_id}\ndata: {json.dumps(ev)}\n\n"

    # `text/event-stream` MUST bypass gzip (Iter 317
    # SSEAwareGZipMiddleware already handles this globally).
    return StreamingResponse(gen(), media_type="text/event-stream")
