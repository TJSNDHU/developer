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
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from cto_services.auth import require_admin, create_token
from services.rate_limiter import check_rate_limit, client_ip_from_request
from services.ora_chat import cost_tracker, session as ora_session
from services.ora_chat import house_rules as ora_house_rules
from services.ora_chat import deep_research as ora_deep
from services.ora_chat import codebase_index as ora_codebase
from services.ora_chat.router import (
    classify_intent, resolve, fallback_route, route_config_snapshot,
)
from services.ora_chat.providers import stream_call, one_shot
from services.ora_chat.safety import (
    assemble_system_prompt, KNOWN_COMMANDS, parse_slash_command,
    DEFAULT_HOUSE_RULES, house_rules_soft_warning,
)
from services.ora_chat import slash_commands as slash_dispatch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ora-chat", tags=["ORA Chat (admin)"])


# Iter 212m-239 — Single-user personal context: no hourly message
# cap. Per-minute burst limiter kept as a defensive backstop to catch
# runaway loops / accidental infinite loops, but cost-tier degradation
# (see cost_tracker.budget_status) is the real spend brake.
_BURST_PER_MIN = 20


# ── Session endpoints ──────────────────────────────────────────────
def _valid_tz(tz: Optional[str]) -> Optional[str]:
    """Whitelist-lite validation — IANA TZ names are simple ASCII with
    slashes/underscores/hyphens. Reject anything that could be a
    prompt-injection vector via the header (we're going to render
    this into the LLM prompt, so untrusted).
    """
    if not tz or not isinstance(tz, str):
        return None
    if len(tz) > 64:
        return None
    import re as _re
    if not _re.match(r"^[A-Za-z][A-Za-z0-9/_\-+]{0,63}$", tz):
        return None
    return tz


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
                     authorization: Optional[str] = Header(None),
                     x_client_tz: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    user_tz = _valid_tz(x_client_tz)

    # Rate + budget gates apply to slash-commands too (they still touch DB).
    ip = client_ip_from_request(request)
    if not check_rate_limit(f"ora_chat:min:{user['user_id']}:{ip}", _BURST_PER_MIN):
        raise HTTPException(429, "Burst limit — slow down for a minute")

    parsed = parse_slash_command(body.command.strip())
    if not parsed:
        raise HTTPException(400, {
            "error": "unknown_command",
            "known": list(KNOWN_COMMANDS),
        })
    cmd, args = parsed

    # Budget-mode gates — spike hard-stop is the ONLY thing that fully blocks.
    b_status = await cost_tracker.budget_status()
    if b_status["mode"] == "spike_hard_stop":
        raise HTTPException(402, {
            "error":   "spike_hard_stop",
            "message": (f"Daily spend spike detected "
                         f"(${b_status['day_spent_usd']} > "
                         f"${b_status['spike_cap_usd']}). Chat is paused "
                         "until you override or the day rolls over."),
            "budget":  b_status,
        })
    # Economy mode still runs slash (DB is free) but skips the LLM
    # explain (which would otherwise consume more tokens).
    if body.explain and b_status["mode"] == "economy":
        body.explain = False

    # Run the deterministic query. NEVER let LLM decide the fetch.
    result = await slash_dispatch.run_slash_command(cmd, args, ctx=user)

    summary_text = ""
    llm_meta: dict = {}
    if body.explain and result.get("ok"):
        cfg = resolve("slash_explain")
        # Fetch the caller's house rules so the explain sentence
        # honors their tone preferences too.
        hr_text = await ora_house_rules.get_effective_text(user["user_id"])
        system_prompt = assemble_system_prompt(hr_text, user_tz=user_tz)
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
            messages=[{"role": "system", "content": system_prompt},
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
        "budget_mode": b_status["mode"],
    }


# ── Streaming message endpoint ──────────────────────────────────────
class MessageBody(BaseModel):
    session_id: str
    content: str = Field(..., min_length=1, max_length=4000)


@router.post("/message")
async def send_message(body: MessageBody,
                        request: Request,
                        authorization: Optional[str] = Header(None),
                        x_client_tz: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    user_tz = _valid_tz(x_client_tz)

    # Rate + budget gates.
    ip = client_ip_from_request(request)
    if not check_rate_limit(f"ora_chat:min:{user['user_id']}:{ip}", _BURST_PER_MIN):
        raise HTTPException(429, "Burst limit — slow down for a minute")
    b_status = await cost_tracker.budget_status()
    if b_status["mode"] == "spike_hard_stop":
        # HTTP 402 → frontend renders a clear inline block, not a toast.
        raise HTTPException(402, {
            "error":    "spike_hard_stop",
            "message":  (f"Daily spend spike detected "
                          f"(${b_status['day_spent_usd']} > "
                          f"${b_status['spike_cap_usd']}). Chat is paused "
                          "until you override or the day rolls over."),
            "budget":   b_status,
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
        return await _stream_slash_result(user, sess, body.content.strip(), parsed, b_status, user_tz)

    # Iter 212m-245 — Auto Deep-Research pre-check.
    # Run the multi-label classifier BEFORE the single-route regex.
    # Only fire the multi-source path if >=2 substantive labels match
    # (or NEEDS_DEEP is explicit). Otherwise fall through to the
    # existing single-route flow — keeps single-topic queries cheap.
    # Skip entirely in `economy` mode (budget-degraded, single-source
    # only) and when the Claude tool_orchestration flag is on (the
    # follow-up will route to Anthropic direct instead — stub returns
    # False today so this branch is inert).
    if b_status["mode"] != "economy" and not ora_deep.use_claude_tools():
        try:
            labels = await ora_deep.classify_labels(body.content)
        except Exception as e:
            logger.warning("deep-research classifier failed: %s", e)
            labels = []
        if labels and await ora_deep.should_go_deep(labels):
            return await _stream_deep_research(
                user, sess, body.content, labels, b_status, user_tz,
            )

    # Regular chat — pick route via keyword rules.
    route_name = classify_intent(body.content)
    # Iter 212m-239 — Economy mode forces GLM-5.2 fallback for all
    # non-slash chat so the assistant NEVER stops working. Full model
    # routing resumes at the next daily rollover.
    if b_status["mode"] == "economy":
        route_name = fallback_route()
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
    # System prompt with layered house rules — safety layer FIRST.
    hr_text = await ora_house_rules.get_effective_text(user["user_id"])
    # Iter 212m-246 — inject compact codebase tree so ORA has
    # baseline awareness of what modules exist without needing a
    # slash-command per question.
    # Iter 212m-249 — ALSO inject the curated system-highlights block
    # so meta-questions ("kya best build hai", "what does AUREM do")
    # get answered from ground truth, not from noisy BM25 hits.
    try:
        cb_tree = await ora_codebase.compact_tree(max_files=120)
        highlights = await ora_codebase.system_highlights()
        cb_tree = f"{highlights}\n\n{cb_tree}" if cb_tree else highlights
    except Exception:
        cb_tree = None
    system_prompt = assemble_system_prompt(hr_text, user_tz=user_tz,
                                            codebase_tree=cb_tree)
    llm_messages = [{"role": "system", "content": system_prompt}] + llm_messages

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
                "model": cfg["model"], "temperature": cfg["temperature"],
                "budget_mode": b_status["mode"]}
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
                                raw_text: str, parsed: tuple[str, str],
                                b_status: dict,
                                user_tz: Optional[str] = None):
    """Wrap the deterministic slash path in the same SSE envelope so
    the frontend has one code path for messages."""
    cmd, args = parsed

    async def event_stream():
        yield {"type": "route", "route": "slash", "model": "deterministic",
                "temperature": 0.0, "budget_mode": b_status["mode"]}
        try:
            result = await slash_dispatch.run_slash_command(cmd, args, ctx=user)
        except KeyError:
            yield {"type": "error", "error": "unknown_command"}
            return
        # Serialize the DB result deterministically first so the UI
        # can render structured data even if the LLM explain fails.
        yield {"type": "slash_result", "command": cmd, "result": result}

        # Optional low-temp explain sentence (skipped in economy/spike).
        explain_text = ""
        summary_usage: dict = {}
        chosen = resolve("slash_explain")
        if b_status["mode"] not in ("economy", "spike_hard_stop") and result.get("ok"):
            hr_text = await ora_house_rules.get_effective_text(user["user_id"])
            system_prompt = assemble_system_prompt(hr_text, user_tz=user_tz)
            msg = (
                f"A slash-command just ran. Explain this result in ONE "
                f"crisp sentence. Do NOT invent numbers.\n\n"
                f"COMMAND: /{cmd}\nRESULT: {json.dumps(result.get('value'))}\n"
                f"METRIC LABEL: {result.get('metric', '')}"
            )
            text, usage, err = await one_shot(
                model=chosen["model"],
                messages=[{"role": "system", "content": system_prompt},
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


# ── Deep Research streaming envelope (Iter 212m-245) ───────────────
_DELTA_CHUNK_LEN = 48  # chars per synthetic delta so the UI streams smoothly


def _labels_to_source_tag(fired: list[str]) -> str:
    """Turn `['github', 'web']` into `github+web` for the route badge."""
    order = ["github", "social", "news", "web"]
    seen: list[str] = []
    for tag in order:
        if tag in fired and tag not in seen:
            seen.append(tag)
    for tag in fired:
        if tag not in seen:
            seen.append(tag)
    return "+".join(seen) if seen else "none"


async def _stream_deep_research(user: dict, sess: dict,
                                 raw_text: str, labels: list[str],
                                 b_status: dict,
                                 user_tz: Optional[str] = None):
    """SSE envelope for the multi-source deep-research path.

    Semantics:
      - Persist the user turn immediately.
      - Emit a `route` event with `route="deep"` + `sources` string.
      - Call `orchestrate()` which fires up to 4 tools in parallel then
        does ONE DeepSeek V3 synthesis pass.
      - Chunk the synthesized text into ~48-char deltas so the UI
        renders progressively (orchestrate itself returns full text —
        it's a one-shot call, not a stream).
      - Log cost via `cost_tracker.log_call` so daily budget accrues.
      - Emit a `final` event with `sources_fired`, `downgraded`,
        `tool_cost_usd` so the frontend badge can show what actually ran.
    """
    session_id = sess["session_id"]

    # Persist user turn before we start — transcript stays honest if
    # any tool fails mid-flight.
    await ora_session.append_message(
        session_id, user["user_id"],
        role="user", content=raw_text,
    )

    hr_text = await ora_house_rules.get_effective_text(user["user_id"])
    try:
        cb_tree = await ora_codebase.compact_tree(max_files=120)
        highlights = await ora_codebase.system_highlights()
        cb_tree = f"{highlights}\n\n{cb_tree}" if cb_tree else highlights
    except Exception:
        cb_tree = None

    async def event_stream():
        # Announce the route up front — sources list will be
        # patched in the `final` event since we don't yet know which
        # tools succeeded until orchestrate returns.
        cfg = resolve("deep")
        yield {"type": "route", "route": "deep",
                "model": cfg["model"], "temperature": cfg["temperature"],
                "labels": labels,
                "budget_mode": b_status["mode"]}

        try:
            out = await ora_deep.orchestrate(
                query=raw_text, labels=labels,
                house_rules_text=hr_text, user_tz=user_tz,
                codebase_tree=cb_tree,
            )
        except Exception as e:
            logger.exception("deep-research orchestrator crashed")
            yield {"type": "error", "error": f"deep_research_failed: {type(e).__name__}"}
            out = {"ok": False, "text": "", "sources_fired": [],
                    "errors": [str(e)], "tool_cost_usd": 0.0,
                    "downgraded": False}

        text = out.get("text") or ""
        sources_fired = out.get("sources_fired") or []
        sources_tag = _labels_to_source_tag(sources_fired)

        # Emit an updated `route` event now that we know which tools
        # fired — frontend uses this to render `deep · github+web`.
        yield {"type": "route", "route": "deep",
                "model": cfg["model"], "temperature": cfg["temperature"],
                "sources": sources_tag,
                "sources_fired": sources_fired,
                "downgraded": bool(out.get("downgraded")),
                "budget_mode": b_status["mode"]}

        # Chunk the response into synthetic deltas so the UI paints
        # progressively (better perceived latency).
        if text:
            for i in range(0, len(text), _DELTA_CHUNK_LEN):
                yield {"type": "delta", "content": text[i:i+_DELTA_CHUNK_LEN]}
                await asyncio.sleep(0)
        elif not out.get("ok"):
            fallback_msg = ("Sources didn't respond in time. Try again "
                             "in a moment, or narrow the question.")
            for i in range(0, len(fallback_msg), _DELTA_CHUNK_LEN):
                yield {"type": "delta", "content": fallback_msg[i:i+_DELTA_CHUNK_LEN]}
            text = fallback_msg

        # Book the synthesis LLM call against the daily budget.
        synth_usage = out.get("synthesis_usage") or {}
        cost = 0.0
        if synth_usage:
            cost = await cost_tracker.log_call(
                user_id=user["user_id"],
                session_id=session_id,
                route="deep",
                model=out.get("synthesis_model") or cfg["model"],
                temperature=cfg["temperature"],
                input_tokens=synth_usage.get("input_tokens", 0),
                output_tokens=synth_usage.get("output_tokens", 0),
            )

        await ora_session.append_message(
            session_id, user["user_id"],
            role="assistant", content=text,
            route="deep",
            model=out.get("synthesis_model") or cfg["model"],
            temperature=cfg["temperature"],
            input_tokens=synth_usage.get("input_tokens", 0),
            output_tokens=synth_usage.get("output_tokens", 0),
            cost_usd=cost,
        )

        yield {"type": "final",
                "cost_usd":       cost,
                "tool_cost_usd":  round(float(out.get("tool_cost_usd") or 0.0), 6),
                "sources":        sources_tag,
                "sources_fired":  sources_fired,
                "downgraded":     bool(out.get("downgraded")),
                "errors":         out.get("errors") or [],
                "input_tokens":   synth_usage.get("input_tokens", 0),
                "output_tokens":  synth_usage.get("output_tokens", 0)}

    async def sse_events():
        async for evt in event_stream():
            yield {"event": evt["type"], "data": json.dumps(evt)}
            await asyncio.sleep(0)

    return EventSourceResponse(sse_events())
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
        "burst_per_min": _BURST_PER_MIN,
    }


# ── House Rules endpoints (Iter 212m-239) ──────────────────────────
class HouseRulesBody(BaseModel):
    rules_text: str = Field(..., max_length=2000)


@router.get("/house-rules")
async def get_house_rules(authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    current = await ora_house_rules.get_current(user["user_id"])
    return {
        "ok":            True,
        "current":       current,
        "effective_text": await ora_house_rules.get_effective_text(user["user_id"]),
        "default_text":  DEFAULT_HOUSE_RULES,
        "max_len":       ora_house_rules.MAX_LEN,
    }


@router.put("/house-rules")
async def put_house_rules(body: HouseRulesBody,
                           authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    try:
        out = await ora_house_rules.update(user["user_id"], body.rules_text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return out


@router.get("/house-rules/history")
async def get_house_rules_history(authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    hist = await ora_house_rules.list_history(user["user_id"])
    return {"ok": True, "history": hist}


@router.post("/house-rules/restore/{version}")
async def restore_house_rules(version: int,
                                authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    try:
        out = await ora_house_rules.restore(user["user_id"], version)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return out


@router.post("/house-rules/reset")
async def reset_house_rules(authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    return await ora_house_rules.reset_to_default(user["user_id"])


# ── PIN login for the public /ora quick-access route (Iter 212m-241) ──
# The route auremcto.com/ora is deliberately unauthenticated at the
# HTML layer so the founder can bookmark it on any device and reach
# ORA in one tap. Security lives in this endpoint:
#   1. Rate-limited by IP — 5 attempts / hour, then hard 429
#   2. PIN compared to `ORA_QUICK_PIN` env (constant-time hmac.compare_digest)
#   3. On success, a real admin JWT is minted (7-day expiry, same as
#      login flow) bound to the founder account resolved from Mongo.
#   4. If no founder row is found (fresh install) we refuse — never
#      auto-privilege escalate.
class PinLoginBody(BaseModel):
    pin: str = Field(..., min_length=1, max_length=16)


@router.post("/pin-login")
async def pin_login(body: PinLoginBody,
                     request: Request):
    ip = client_ip_from_request(request)
    # Coarse hourly counter over `ora_chat_pin_attempts` — one aggregate.
    from cto_services.db import get_db
    import hmac, time as _time
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")
    cutoff = _time.time() - 3600
    n_fail = await db.ora_chat_pin_attempts.count_documents({
        "ip": ip, "ok": False, "ts": {"$gte": cutoff},
    })
    if n_fail >= 5:
        raise HTTPException(429, {
            "error":   "too_many_attempts",
            "message": "Too many wrong PIN attempts. Try again in an hour.",
        })

    expected = os.getenv("ORA_QUICK_PIN", "").strip()
    if not expected:
        raise HTTPException(503, "PIN login not configured")
    ok = hmac.compare_digest(body.pin.strip(), expected)

    await db.ora_chat_pin_attempts.insert_one({
        "ip": ip, "ok": ok, "ts": _time.time(),
    })
    if not ok:
        remaining = max(0, 5 - (n_fail + 1))
        raise HTTPException(401, {
            "error":              "invalid_pin",
            "attempts_remaining": remaining,
        })

    # Resolve the founder → mint a real admin JWT tied to that identity.
    # Never falls back to a random admin (privilege-escalation risk).
    #
    # Iter 212m-248 — Production PIN was failing 503 because it only
    # trusted the `is_founder=True` DB flag. That flag isn't reliably
    # backfilled on prod Mongo; the authoritative signal is
    # `FOUNDER_EMAILS` (env, with a hardcoded fallback for the company
    # founder in services/usage.py::founder_emails()). So we now:
    #   1. Look up any dev_users row whose email is in the trusted
    #      founder set.
    #   2. Fall back to the legacy `is_founder=True` flag.
    #   3. If a founder row is found but lacks the flag, backfill it
    #      idempotently so downstream code stays consistent.
    from services.usage import founder_emails as _founder_emails_set
    trusted = list(_founder_emails_set())
    founder = None
    if trusted:
        founder = await db.dev_users.find_one(
            {"email": {"$in": trusted}},
            {"user_id": 1, "email": 1, "is_admin": 1, "is_founder": 1, "_id": 0},
        )
    if not founder:
        founder = await db.dev_users.find_one(
            {"is_founder": True},
            {"user_id": 1, "email": 1, "is_admin": 1, "is_founder": 1, "_id": 0},
        )
    if not founder:
        # Fresh install / seed missing — refuse rather than issue a
        # token that could bind to whoever we pick.
        raise HTTPException(503, "Founder identity not configured")

    # Idempotent backfill: keep the `is_founder` DB flag in sync with
    # the env-declared founder identity. Safe because we only reach
    # here after a valid PIN + trusted-email lookup.
    if not founder.get("is_founder"):
        try:
            await db.dev_users.update_one(
                {"user_id": founder["user_id"]},
                {"$set": {"is_founder": True, "is_admin": True}},
            )
        except Exception as e:                                # noqa: BLE001
            logger.warning("founder flag backfill failed: %r", e)

    token = create_token(
        user_id=founder["user_id"],
        email=founder["email"],
        is_admin=True,
    )
    return {
        "ok":         True,
        "token":      token,
        "expires_in": 86400 * 7,
        "user":       {"email": founder["email"], "is_admin": True},
    }
