"""
routers/ora_chat.py — Iter 212m-238

API endpoints for the internal admin ORA Chat.

Endpoints (all under `/api/aurem-dev/ora-chat/`):
    POST   /sessions               — create new session
    GET    /sessions               — list sessions for the caller
    GET    /sessions/{session_id}  — full transcript (owner only)
    POST   /message                — send a message; returns SSE stream
    POST   /slash                  — run a slash-command (JSON reply)
    GET    /usage                  — this month's budget + spend snapshot
    GET    /config                 — live route/temperature config (for tests + admin UI)

All endpoints gate on `require_admin` — founder + admin flags only.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from cto_services.auth import require_admin
from services.rate_limiter import check_rate_limit, client_ip_from_request
from services.ora_chat import cost_tracker, session as ora_session
from services.ora_chat.router import (
    classify_intent, resolve, fallback_route, route_config_snapshot,
)
from services.ora_chat.providers import stream_call, one_shot
from services.ora_chat.safety import (
    SYSTEM_PROMPT, KNOWN_COMMANDS, parse_slash_command,
)
from services.ora_chat import slash_commands as slash_dispatch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ora-chat", tags=["ORA Chat (admin)"])


# ── Rate-limit helper ───────────────────────────────────────────────
# 30 messages/hour == 0.5/minute; the underlying limiter uses a 60s
# sliding window so we approximate hourly by running a stricter
# per-minute check and enforcing a coarser hourly counter via Mongo.
_HOURLY_LIMIT = 30


async def _hourly_ok(user_id: str) -> tuple[bool, int]:
    """Returns (allowed, remaining_this_hour). Coarse hourly counter
    over `ora_chat_usage` — one aggregate query."""
    from cto_services.db import get_db
    import time as _time
    db = get_db()
    if db is None:
        return True, _HOURLY_LIMIT
    cutoff = _time.time() - 3600
    try:
        n = await db.ora_chat_usage.count_documents(
            {"user_id": user_id, "ts": {"$gte": cutoff}},
        )
    except Exception:
        return True, _HOURLY_LIMIT
    remaining = max(0, _HOURLY_LIMIT - int(n))
    return (n < _HOURLY_LIMIT, remaining)


# ── Session endpoints ──────────────────────────────────────────────
class NewSessionBody(BaseModel):
    title: Optional[str] = Field(default="", max_length=80)


@router.post("/sessions")
async def create_session(body: NewSessionBody,
                          authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    doc = await ora_session.create_session(user["user_id"], body.title or "")
    return {"ok": True, "session": doc}


@router.get("/sessions")
async def list_sessions(authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    rows = await ora_session.list_sessions(user["user_id"])
    return {"ok": True, "sessions": rows}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str,
                       authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    doc = await ora_session.get_session(session_id, user["user_id"])
    if not doc:
        raise HTTPException(404, "Session not found")
    return {"ok": True, "session": doc}


# ── Slash command endpoint (deterministic path) ─────────────────────
class SlashBody(BaseModel):
    command: str = Field(..., pattern=r"^/?[a-z][a-z0-9\-]*(?:\s.*)?$")
    session_id: Optional[str] = None
    explain: bool = True  # if True, run the low-temp LLM formatter


@router.post("/slash")
async def run_slash(body: SlashBody,
                     request: Request,
                     authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)

    # Rate + budget gates apply to slash-commands too (they still touch DB).
    ip = client_ip_from_request(request)
    if not check_rate_limit(f"ora_chat:min:{user['user_id']}:{ip}", 10):
        raise HTTPException(429, "Too many requests — slow down for a minute")
    ok, remaining = await _hourly_ok(user["user_id"])
    if not ok:
        raise HTTPException(429, f"Hourly limit ({_HOURLY_LIMIT}) reached — try again in an hour")

    parsed = parse_slash_command(body.command.strip())
    if not parsed:
        raise HTTPException(400, {
            "error": "unknown_command",
            "known": list(KNOWN_COMMANDS),
        })
    cmd, args = parsed

    # Budget check — slash commands with `explain=true` still cost a
    # tiny LLM call for the summary sentence.
    if body.explain and await cost_tracker.is_over_budget():
        # Slash still runs the DB query — it costs $0 — but skip the
        # LLM summary so we don't cross the cap.
        body.explain = False

    # Run the deterministic query. NEVER let LLM decide the fetch.
    result = await slash_dispatch.run_slash_command(cmd, args, ctx=user)

    summary_text = ""
    llm_meta: dict = {}
    if body.explain and result.get("ok"):
        cfg = resolve("slash_explain")
        # Feed the JSON result as data; explicit instructions say
        # "explain, do not invent numbers".
        msg = (
            f"A slash-command just ran. Explain this result in ONE crisp "
            f"sentence (Hinglish is fine). Do NOT invent numbers. Do NOT "
            f"suggest running other commands.\n\n"
            f"COMMAND: /{cmd}\n"
            f"RESULT: {json.dumps(result.get('value'))}\n"
            f"METRIC LABEL: {result.get('metric', '')}"
        )
        text, usage, err = await one_shot(
            model=cfg["model"],
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user",   "content": msg}],
            temperature=cfg["temperature"],
            top_p=cfg["top_p"],
            presence_penalty=cfg["presence_penalty"],
            max_tokens=cfg["max_tokens"],
        )
        summary_text = text or ""
        if not err and usage:
            cost = await cost_tracker.log_call(
                user_id=user["user_id"],
                session_id=body.session_id or "slash",
                route=cfg["route"],
                model=cfg["model"],
                temperature=cfg["temperature"],
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
            llm_meta = {"cost_usd": cost, **usage,
                         "temperature": cfg["temperature"],
                         "model": cfg["model"]}

    # Persist to session transcript (best-effort — slash still works
    # even when session_id is unknown, so ops can eval commands
    # without opening a chat window).
    if body.session_id:
        await ora_session.append_message(
            body.session_id, user["user_id"],
            role="user", content=body.command,
        )
        await ora_session.append_message(
            body.session_id, user["user_id"],
            role="assistant",
            content=summary_text or json.dumps(result.get("value")),
            route=llm_meta.get("route", "slash"),
            model=llm_meta.get("model", ""),
            temperature=llm_meta.get("temperature"),
            input_tokens=llm_meta.get("input_tokens", 0),
            output_tokens=llm_meta.get("output_tokens", 0),
            cost_usd=llm_meta.get("cost_usd", 0.0),
        )

    return {
        "ok":       True,
        "command":  cmd,
        "result":   result,
        "summary":  summary_text,
        "llm":      llm_meta,
        "remaining_this_hour": remaining - 1,
    }


# ── Streaming message endpoint ──────────────────────────────────────
class MessageBody(BaseModel):
    session_id: str
    content: str = Field(..., min_length=1, max_length=4000)


@router.post("/message")
async def send_message(body: MessageBody,
                        request: Request,
                        authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)

    # Rate + budget gates.
    ip = client_ip_from_request(request)
    if not check_rate_limit(f"ora_chat:min:{user['user_id']}:{ip}", 10):
        raise HTTPException(429, "Too many requests — slow down for a minute")
    ok, _remaining = await _hourly_ok(user["user_id"])
    if not ok:
        raise HTTPException(429, f"Hourly limit ({_HOURLY_LIMIT}) reached — try again in an hour")
    if await cost_tracker.is_over_budget():
        # HTTP 402 → frontend renders a clear inline block, not a toast.
        status = await cost_tracker.budget_status()
        raise HTTPException(402, {
            "error":    "budget_exceeded",
            "message":  "This month's ORA budget is used up. Resets on the 1st.",
            "budget":   status,
        })

    # Session ownership check.
    sess = await ora_session.get_session(body.session_id, user["user_id"])
    if not sess:
        raise HTTPException(404, "Session not found")

    # If the message is a slash-command, we short-circuit to /slash
    # semantics but keep the same streaming envelope so the frontend
    # only needs one path.
    parsed = parse_slash_command(body.content.strip())
    if parsed:
        return await _stream_slash_result(user, sess, body.content.strip(), parsed)

    # Regular chat — pick route via keyword rules.
    route_name = classify_intent(body.content)
    cfg = resolve(route_name)

    # Persist the user turn IMMEDIATELY so the transcript stays honest
    # even if the stream dies mid-response.
    await ora_session.append_message(
        body.session_id, user["user_id"],
        role="user", content=body.content,
    )

    # Refresh rolling summary if the window overflowed with the new turn.
    await ora_session.maybe_update_summary(body.session_id, user["user_id"])

    # Rebuild the LLM message list (system + summary + last 6 turns).
    sess = await ora_session.get_session(body.session_id, user["user_id"])
    llm_messages = await ora_session.build_llm_messages(sess or {})
    # System prompt is always the last-added, first-in-array element.
    llm_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + llm_messages

    async def event_stream():
        buf: list[str] = []
        usage: dict = {}
        errored: Optional[str] = None
        fallback_used = False

        async def _try_stream(model_cfg: dict):
            nonlocal errored, usage
            errored = None
            async for evt in stream_call(
                model=model_cfg["model"],
                messages=llm_messages,
                temperature=model_cfg["temperature"],
                top_p=model_cfg["top_p"],
                presence_penalty=model_cfg["presence_penalty"],
                max_tokens=model_cfg["max_tokens"],
            ):
                if evt["type"] == "delta":
                    buf.append(evt["content"])
                    yield evt
                elif evt["type"] == "usage":
                    usage = {k: evt[k] for k in
                              ("input_tokens", "output_tokens") if k in evt}
                elif evt["type"] == "error":
                    errored = evt.get("error", "unknown")
                    yield evt
                elif evt["type"] == "done":
                    yield evt

        # Announce chosen model up front so the UI can badge it.
        yield {"type": "route", "route": cfg["route"],
                "model": cfg["model"], "temperature": cfg["temperature"]}
        async for evt in _try_stream(cfg):
            yield evt

        # Fallback path — only if primary produced ZERO content.
        if errored and not buf:
            fb_cfg = resolve(fallback_route())
            fallback_used = True
            yield {"type": "route", "route": fb_cfg["route"],
                    "model": fb_cfg["model"], "temperature": fb_cfg["temperature"],
                    "reason": "primary_failed"}
            async for evt in _try_stream(fb_cfg):
                yield evt

        # Persist assistant turn + log usage — even if empty/errored,
        # so the transcript reflects reality.
        final_text = "".join(buf)
        chosen = resolve(fallback_route()) if fallback_used else cfg
        cost = 0.0
        if usage:
            cost = await cost_tracker.log_call(
                user_id=user["user_id"],
                session_id=body.session_id,
                route=chosen["route"],
                model=chosen["model"],
                temperature=chosen["temperature"],
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                error=errored,
            )
        await ora_session.append_message(
            body.session_id, user["user_id"],
            role="assistant", content=final_text,
            route=chosen["route"], model=chosen["model"],
            temperature=chosen["temperature"],
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cost_usd=cost,
        )
        yield {"type": "final", "cost_usd": cost,
                "input_tokens":  usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "errored": errored}

    async def sse_events():
        async for evt in event_stream():
            yield {"event": evt["type"], "data": json.dumps(evt)}

    return EventSourceResponse(sse_events())


async def _stream_slash_result(user: dict, sess: dict,
                                raw_text: str, parsed: tuple[str, str]):
    """Wrap the deterministic slash path in the same SSE envelope so
    the frontend has one code path for messages."""
    cmd, args = parsed

    async def event_stream():
        yield {"type": "route", "route": "slash", "model": "deterministic",
                "temperature": 0.0}
        try:
            result = await slash_dispatch.run_slash_command(cmd, args, ctx=user)
        except KeyError:
            yield {"type": "error", "error": "unknown_command"}
            return
        # Serialize the DB result deterministically first so the UI
        # can render structured data even if the LLM explain fails.
        yield {"type": "slash_result", "command": cmd, "result": result}

        # Optional low-temp explain sentence (skipped if over budget).
        explain_text = ""
        summary_usage: dict = {}
        chosen = resolve("slash_explain")
        if not await cost_tracker.is_over_budget() and result.get("ok"):
            msg = (
                f"A slash-command just ran. Explain this result in ONE "
                f"crisp sentence. Do NOT invent numbers.\n\n"
                f"COMMAND: /{cmd}\nRESULT: {json.dumps(result.get('value'))}\n"
                f"METRIC LABEL: {result.get('metric', '')}"
            )
            text, usage, err = await one_shot(
                model=chosen["model"],
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user",   "content": msg}],
                temperature=chosen["temperature"],
                top_p=chosen["top_p"],
                presence_penalty=chosen["presence_penalty"],
                max_tokens=chosen["max_tokens"],
            )
            explain_text = text or ""
            if not err and usage:
                summary_usage = usage
                yield {"type": "delta", "content": explain_text}

        # Persist both turns.
        await ora_session.append_message(
            sess["session_id"], user["user_id"],
            role="user", content=raw_text,
        )
        cost = 0.0
        if summary_usage:
            cost = await cost_tracker.log_call(
                user_id=user["user_id"],
                session_id=sess["session_id"],
                route=chosen["route"], model=chosen["model"],
                temperature=chosen["temperature"],
                input_tokens=summary_usage.get("input_tokens", 0),
                output_tokens=summary_usage.get("output_tokens", 0),
            )
        await ora_session.append_message(
            sess["session_id"], user["user_id"],
            role="assistant",
            content=explain_text or json.dumps(result.get("value")),
            route=chosen["route"], model=chosen["model"],
            temperature=chosen["temperature"],
            input_tokens=summary_usage.get("input_tokens", 0),
            output_tokens=summary_usage.get("output_tokens", 0),
            cost_usd=cost,
        )
        yield {"type": "final", "cost_usd": cost,
                **{k: summary_usage.get(k, 0) for k in
                    ("input_tokens", "output_tokens")}}

    async def sse_events():
        async for evt in event_stream():
            yield {"event": evt["type"], "data": json.dumps(evt)}
            await asyncio.sleep(0)

    return EventSourceResponse(sse_events())


# ── Usage + config endpoints (for admin dashboard) ──────────────────
@router.get("/usage")
async def usage(authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    return {"ok": True, "budget": await cost_tracker.budget_status()}


@router.get("/config")
async def config(authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    return {
        "ok":            True,
        "routes":        route_config_snapshot(),
        "known_commands": list(KNOWN_COMMANDS),
        "hourly_limit":  _HOURLY_LIMIT,
    }
