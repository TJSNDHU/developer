"""
routers/chat.py — AUREM Dev
AI chat endpoints: send (sync), stream (SSE), history, sessions.
All messages persisted to db.chat_sessions per user.
First assistant reply triggers a background title-summarization.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, validator

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.orchestrator import chat_with_tools
from services.llm import call_llm_with_meta, call_emergent_watchdog, cap_for
from services.repo_context import get_repo_context
from services.url_fetcher import build_url_context

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


# Heuristic: prompt mentions build/create/fix/write code/etc → bump cap
_CODE_HINTS = ("```", "build", "create", "fix", "write", "implement",
               "function", "class", "refactor", "debug", "snippet", "code")


def _detect_mode(prompt: str) -> str:
    p = (prompt or "").lower()
    return "code" if any(h in p for h in _CODE_HINTS) else "chat"


async def _deduct_tokens(user_id: str, reply: str) -> int:
    """Deduct ~1 token per 3 words from the user's wallet. Returns new balance."""
    db = get_db()
    if db is None or not user_id:
        return 0
    used = max(1, len((reply or "").split()) // 3 + 1)
    try:
        await db.dev_users.update_one(
            {"user_id": user_id},
            {"$inc": {"tokens_remaining": -used}},
        )
        u = await db.dev_users.find_one(
            {"user_id": user_id}, {"_id": 0, "tokens_remaining": 1}
        )
        return int((u or {}).get("tokens_remaining", 0))
    except Exception as e:
        logger.warning(f"deduct_tokens failed: {e!r}")
        return 0


class ChatBody(BaseModel):
    # Iter 44 — bounded length to prevent prompt-bomb DoS + match
    # downstream cap_for() context windows.
    prompt: str = Field(..., min_length=1, max_length=20000)
    session_id: Optional[str] = Field(None, max_length=128)
    max_tool_iters: int = Field(4, ge=0, le=12)
    maxx_mode: bool = False
    project_id: Optional[str] = Field(None, max_length=128)
    # Iter 38: agent selector. "auto" routes via existing model-routing
    # logic in orchestrator.py (DeepSeek/Claude). "ora" calls the founder's
    # own aurem.live ORA endpoint. Other values currently fall through to
    # "auto" so adding new agents later is backwards-compatible.
    agent: Optional[str] = Field("auto", max_length=32)
    # Iter 153 — review mode requested by the user (swift/pro/maxx).
    # Server clamps to whatever their tier allows; never trusted as-is.
    mode: Optional[str] = Field("swift", max_length=16)
    # Iter 159 — true when the request originates from the ASK ORA
    # side panel. Triggers the casual ASK-ORA tone override in the
    # system prompt for this turn only; the main coding chat never
    # sets this so its persona stays untouched.
    ora_panel: bool = False
    # Iter 42: structured payload of browser console/network/stack errors
    # captured by frontend/public/F12ErrorCapture.js. When present (and has
    # any errors), the request is auto-classified as Mode D (debug).
    f12_payload: Optional[dict] = None

    @validator("prompt")
    def _strip_prompt(cls, v: str) -> str:
        return (v or "").strip()


# ─────────────────────────────────────────────────────────────────────────────
# Iter 42 — Mode classifier (A/B/C/D/E)
# Centralised so chat.py and the worker share the same logic.
# ─────────────────────────────────────────────────────────────────────────────
import re as _re_mode

_FIX_CONFIRM = _re_mode.compile(
    r"\b(yes|yep|yeah|sure|ok|okay|fix\s+it|ship\s+it|do\s+it|go\s+ahead|apply\s+the\s+fix)\b",
    _re_mode.IGNORECASE,
)

# Iter 50 — short greetings should NEVER be classified as debug just
# because stale F12 errors are still in the browser's capture buffer.
_GREETING = _re_mode.compile(
    r"^\s*(hi|hello|hey|yo|sup|hola|namaste|good\s+(morning|afternoon|evening)|"
    r"thanks|thank\s+you|ok|okay|got\s+it|cool|nice|awesome)"
    r"(\s+\w{0,12}){0,3}\s*[!.?]?\s*$",
    _re_mode.IGNORECASE,
)


def is_fix_confirmation(message: str) -> bool:
    return bool(_FIX_CONFIRM.search(message or ""))


def _f12_has_real_signal(payload: dict) -> bool:
    """Iter 50 — guards against fishing-expedition Mode D triggers when the
    F12 buffer holds only noise (aborted/200 network entries, no stack
    traces, no real console.error messages).

    Iter 105 — also filters out transient proxy / gateway errors with an
    HTML body (Cloudflare 520, gateway 502/504, etc.). These fire on
    cold-start before the origin is ready and would otherwise trigger
    Mode D on the user's very first chat message, producing the spammy
    "Files to check: (unknown — error context too thin)" bailout.

    Returns True only when the payload contains something a debugger
    can actually use:
      * A console error with a non-trivial message (>5 chars)
      * A network error with HTTP status in 400-599 AND a real URL AND
        NOT a transient proxy/gateway code with an HTML body
      * Any stack trace
    """
    if not isinstance(payload, dict):
        return False
    for ce in (payload.get("console_errors") or []):
        msg = (ce.get("message") or ce.get("msg") or "").strip()
        if len(msg) > 5 and "aborted" not in msg.lower():
            return True
    for ne in (payload.get("network_errors") or []):
        st = ne.get("status", 0)
        if not (isinstance(st, int) and 400 <= st < 600 and ne.get("url")):
            continue
        if _is_transient_proxy_error(st, ne.get("response_body", "")):
            continue
        return True
    if payload.get("stack_traces"):
        return True
    return False


# Iter 105 — Cloudflare / proxy / gateway codes whose body is typically a
# generic HTML error page (NOT a real application error). These get
# dropped from F12 signal so a cold-start 520 doesn't poison ORA's
# first-chat response.
_TRANSIENT_PROXY_CODES = {
    408,                                                # Request Timeout
    502, 503, 504,                                      # Bad Gateway / SU / GT
    520, 521, 522, 523, 524, 525, 526, 527, 530,        # Cloudflare-specific
}


def _is_transient_proxy_error(status: int, body) -> bool:
    """Return True when (status, body) looks like a Cloudflare / nginx /
    proxy-level error page rather than a real API 5xx from our backend.
    Defensive: only treat as transient when status IS in the proxy set
    AND body looks like HTML (or is empty — proxy edge cases)."""
    if status not in _TRANSIENT_PROXY_CODES:
        return False
    b = (body or "")
    if isinstance(b, bytes):
        try:
            b = b.decode("utf-8", errors="ignore")
        except Exception:
            b = ""
    if not isinstance(b, str):
        return False
    if not b.strip():
        return True  # empty body on a proxy code → almost certainly a proxy error
    bl = b.lower()
    return ("<!doctype html" in bl) or ("<html" in bl) or ("cloudflare" in bl)


def classify_intent(message: str, f12_payload: Optional[dict]) -> str:
    """Returns one of: 'A','B','C','D','E','F'. Order matters."""
    from services.mode_d_debugger import is_debug_request
    from services.mode_e_auditor  import is_audit_request
    from services.mode_f_engage   import is_engage_request

    # Iter 50 — greeting wins over stale F12 noise. We still SHOW the
    # captured errors to the user via the F12 badge; we just don't
    # fire a hallucination-prone Mode D LLM call on a casual hello.
    msg = (message or "").strip()
    if _GREETING.match(msg):
        return "A"

    if f12_payload and _f12_has_real_signal(f12_payload):
        return "D"
    if is_debug_request(message):
        return "D"
    if is_audit_request(message):
        return "E"
    # Iter 60 — Engage mode catches market / positioning / GTM /
    # competitor / copy questions BEFORE the C/B coding classifiers
    # so a "write me a launch tweet about X" doesn't burn the full
    # codegen orchestrator.
    if is_engage_request(message):
        return "F"

    c_patterns = [
        r"\b(add|create|build|implement|write|generate|make|ship|deploy|fix|update|refactor)\b.*\b(to|in|for)\b.*\b(my|the)\b.*\b(repo|project|app|code|file)\b",
        r"\bship (this|it|the)\b",
        r"\bcommit\b",
        r"\bpush to (github|main|prod)\b",
    ]
    for p in c_patterns:
        if _re_mode.search(p, message or "", _re_mode.IGNORECASE):
            return "C"

    b_patterns = [
        r"\bshould i\b",
        r"\bwhich is better\b",
        r"\bwhat['']s the best way\b",
        r"\bgive me (ideas|suggestions|options)\b",
        r"\bcompare\b",
        r"\brecommend\b",
        r"\bhow should i\b",
        r"\bwhat do you think\b",
        # Iter 81 — stuck-decision phrases. These also fire the Mode B
        # auto-upgrade (Decision Council) downstream.
        r"\btorn between\b",
        r"\bstuck (on|between)\b",
        r"\bcan'?t decide\b",
        r"\bcannot decide\b",
        r"\bdebating between\b",
        r"\b(pivot or persevere|build or buy)\b",
        r"\b(decision )?council\b",
    ]
    for p in b_patterns:
        if _re_mode.search(p, message or "", _re_mode.IGNORECASE):
            return "B"

    return "A"


_TITLE_SYSTEM = "Generate ultra-short chat titles. 3-5 words, Title Case, no punctuation. Just the title."


async def _generate_title(first_user_msg: str) -> str:
    """Ask the LLM to summarize the first user message in 3-5 words.
    Returns "" on any failure so the caller can fall back to last_message."""
    try:
        prompt = f"3-5 word title, Title Case, no punctuation: {first_user_msg.strip()[:100]}"
        meta = await call_llm_with_meta(_TITLE_SYSTEM, prompt,
                                         max_tokens=cap_for("title"),
                                         mode="title")
        title = (meta.get("content") or "").strip()
        title = title.strip("\"'`").rstrip(".!?").strip()
        if not title:
            return ""
        if len(title) > 60:
            title = title[:57].rstrip() + "…"
        return title
    except Exception as e:
        logger.warning(f"title generation failed: {e!r}")
        return ""


async def _maybe_set_title(user_id: str, session_id: str,
                            first_user_msg: str) -> None:
    """If this session has no title yet, generate one and store it.
    Safe to call as a background task (fire-and-forget)."""
    db = get_db()
    if db is None or not session_id:
        return
    try:
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0, "title": 1, "turns": 1},
        )
        if not doc:
            return
        if doc.get("title"):
            return
        if len(doc.get("turns") or []) < 2:
            return
        title = await _generate_title(first_user_msg)
        if not title:
            return
        await db.chat_sessions.update_one(
            {"session_id": session_id, "user_id": user_id},
            {"$set": {"title": title}},
        )
        logger.info(f"titled session {session_id[:8]}…: {title!r}")
    except Exception as e:
        logger.warning(f"_maybe_set_title failed: {e!r}")


async def _persist_turn(user_id: str, session_id: str, user_prompt: str,
                        assistant_reply: str, provider: str,
                        watchdog: Optional[dict] = None,
                        project_id: Optional[str] = None,
                        shipped_task_id: Optional[str] = None) -> None:
    """Append user+assistant turns to db.chat_sessions, capped at 40 turns.
    Tags the session with the project it belongs to (None == Home/global).
    Iter 51 — when `shipped_task_id` is set (e.g. Mode D→C auto-handoff),
    it's pinned on the assistant turn so a refresh keeps the live progress
    card rendered (same contract as /chat/turn/shipped)."""
    db = get_db()
    if db is None or not session_id:
        return
    now = time.time()
    preview = (assistant_reply or "").strip()[:120] or (user_prompt or "")[:120]
    assistant_turn = {
        "role": "assistant", "content": assistant_reply,
        "ts": now, "provider": provider,
    }
    if watchdog:
        assistant_turn["watchdog"] = watchdog
    if shipped_task_id:
        assistant_turn["shipped_task_id"] = shipped_task_id
    set_on_insert = {
        "session_id": session_id,
        "user_id": user_id,
        "created_at": now,
        "project_id": project_id,
    }
    set_fields = {
        "updated_at": now,
        "last_message": preview,
    }
    try:
        await db.chat_sessions.update_one(
            {"session_id": session_id, "user_id": user_id},
            {
                "$setOnInsert": set_on_insert,
                "$set": set_fields,
                "$push": {
                    "turns": {
                        "$each": [
                            {"role": "user", "content": user_prompt, "ts": now},
                            assistant_turn,
                        ],
                        "$slice": -40,
                    }
                },
            },
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"persist_turn failed: {e!r}")


@router.post("/send")
async def chat_send(
    body: ChatBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Non-streaming chat — returns full response, persists turn.
    If maxx_mode=True, runs Emergent watchdog review after DeepSeek reply."""
    user = await current_dev(authorization)
    jwt_token = authorization.split(" ", 1)[1] if authorization else ""
    repo_ctx = await get_repo_context(user["user_id"], body.project_id or "")
    url_ctx = await build_url_context(body.prompt)
    extra_sys = "\n\n".join(s for s in (repo_ctx, url_ctx) if s)
    # Iter 153 — clamp the requested review mode to whatever the user's
    # tier allows. Falls back to the BEST mode they have access to so
    # the request never errors out from a missing entitlement.
    from services.subscription_tiers import allowed_modes_for_tier
    _allowed = allowed_modes_for_tier((user or {}).get("tier") or "free")
    req_mode = body.mode if (body.mode in _allowed) else _allowed[-1]
    result = await chat_with_tools(
        prompt=body.prompt,
        jwt_token=jwt_token,
        system=(extra_sys + "\n\n" if extra_sys else None),
        max_iters=min(body.max_tool_iters, 4),
        session_id=body.session_id,
        mongo_client=None,
        user_id=user["user_id"],
        project_id=body.project_id,
        mode=req_mode,
    )
    content = result.get("content", "") or ""
    provider = result.get("provider", "") or ""
    mode = _detect_mode(body.prompt)
    from services.llm import temperature_for
    temperature = temperature_for(mode)

    # Maxx mode: watchdog review (only if we have non-empty content)
    watchdog = None
    if body.maxx_mode and content.strip():
        watchdog = await call_emergent_watchdog(content)
        provider = (provider or "deepseek") + "+emergent-watchdog"

    await _persist_turn(user["user_id"], body.session_id or "",
                        body.prompt, content, provider, watchdog=watchdog,
                        project_id=body.project_id)
    if body.session_id:
        asyncio.create_task(
            _maybe_set_title(user["user_id"], body.session_id, body.prompt)
        )
    tokens_remaining = await _deduct_tokens(user["user_id"], content)
    return {
        "ok": result.get("ok", True),
        "content": content,
        "provider": provider,
        "watchdog": watchdog,
        "mode": mode,
        "temperature": temperature,
        "iterations": result.get("iterations", 0),
        "session_id": body.session_id,
        "user_id": user.get("user_id"),
        "tokens_remaining": tokens_remaining,
    }



@router.get("/agents/list")
async def list_agents(authorization: Optional[str] = Header(None)) -> dict:
    """Iter 38: return the agents this user is allowed to pick from in
    the chat selector. ORA is shown only to founder accounts."""
    user = await current_dev(authorization)
    from services.usage import is_founder_email
    from services.ora_client import is_ora_available
    is_founder = is_founder_email(user.get("email"))
    agents = [
        {"id": "auto",  "label": "AUREM",
         "desc": "Auto-routes between Claude (code) and DeepSeek (chat)",
         "default": True},
    ]
    if is_founder and is_ora_available():
        agents.append({
            "id": "ora",  "label": "ORA",
            "desc": "Aurem.live ORA model — founder-only",
            "founder_only": True,
        })
    return {"agents": agents, "default": "auto"}


@router.get("/modes/available")
async def available_modes(authorization: Optional[str] = Header(None)) -> dict:
    """Iter 153 — return the review-mode catalog with lock-state for the
    caller's tier. Drives ModeSelector.jsx in the composer."""
    user = await current_dev(authorization)
    from services.subscription_tiers import allowed_modes_for_tier
    tier = (user or {}).get("tier") or "free"
    allowed = allowed_modes_for_tier(tier)
    catalog = {
        "swift": {
            "label": "Swift", "min_tier": "starter", "price": "$9",
            "desc": "Fast code with a quick safety check. Best for everyday work.",
        },
        "pro": {
            "label": "Pro", "min_tier": "pro", "price": "$19",
            "desc": "DeepSeek + Claude review every answer. Higher quality.",
        },
        "maxx": {
            "label": "Maxx", "min_tier": "team", "price": "$49",
            "desc": "Claude writes your code directly. Best for critical work.",
        },
    }
    out = {k: {**v, "unlocked": k in allowed} for k, v in catalog.items()}
    return {"ok": True, "tier": tier, "modes": out}



# ── Iter 87: "ship" shortcut helper ─────────────────────────────────────
# When the prior assistant turn already emitted an ```aurem-handoff
# fence and the new user prompt is a short confirmation ("ship",
# "do it", "go", "yes", etc.), queue the cto_task directly from the
# prior brief. This skips the orchestrator entirely — no second
# reasoning loop, no second tool call budget, no 90 s wall.

# Phrases that mean "execute the previous handoff brief" and ONLY that.
# Short list on purpose — if the user types anything substantive we
# want the normal reasoning loop, not a silent ship.
_SHIP_CONFIRMATIONS = {
    "ship", "ship it", "ship via cto", "do it", "do it now",
    "go", "go ahead", "yes", "yep", "ok", "okay", "proceed",
    "please ship", "ship please", "send it", "execute", "run it",
}

_HANDOFF_FENCE_RE = re.compile(
    r"```aurem-handoff\s*\n([\s\S]*?)```",
    re.MULTILINE,
)


def _normalise_confirmation(prompt: str) -> str:
    return (prompt or "").strip().lower().rstrip(".!?")


def _looks_like_ship_confirmation(prompt: str) -> bool:
    p = _normalise_confirmation(prompt)
    if not p or len(p) > 30:
        return False
    return p in _SHIP_CONFIRMATIONS


async def _maybe_ship_shortcut(*, body, user_id: str, repo_ctx: str):
    """Return an async generator that streams the ship-shortcut result,
    or None when the shortcut doesn't apply (caller falls through to
    the normal orchestrator path)."""
    if not _looks_like_ship_confirmation(body.prompt):
        return None
    db = get_db()
    if db is None:
        return None
    sess = await db.chat_sessions.find_one(
        {"user_id": user_id, "session_id": body.session_id},
        {"messages": 1, "_id": 0},
    )
    msgs = (sess or {}).get("messages") or []
    # Walk back to find the most recent assistant turn with a handoff fence.
    brief = None
    for m in reversed(msgs):
        if m.get("role") != "assistant":
            continue
        match = _HANDOFF_FENCE_RE.search(m.get("content") or "")
        if match:
            brief = match.group(1).strip()
            break
    if not brief:
        return None

    # Stream a small confirmation turn and queue the task.
    async def _stream():
        import time as _t
        t_start = _t.monotonic()
        meta = {
            "meta": True,
            "session_id": body.session_id,
            "provider": "aurem-ship-shortcut",
            "mode": "C",
            "temperature": 0.0,
            "thinking_s": 0.0,
            "tool_calls_run": 0,
            "ship_shortcut": True,
        }
        yield f"data: {json.dumps(meta)}\n\n"

        # Iter 132 — Mode C ship shortcut tick emission. Without periodic
        # tick frames the chat UI shows "Thinking…" with no elapsed timer
        # while _enqueue_cto_task runs (GitHub repo checks, etc. — can take
        # several seconds). We run the heavy work as a background task and
        # interleave {thinking:true, elapsed_s, activity} frames every 0.5s
        # so MessageBubble.jsx renders the live counter exactly like the
        # normal chat_with_tools path.
        stop_event = asyncio.Event()
        activity = {"label": "queueing ship task…"}

        def _emit_tick() -> str:
            elapsed = round(_t.monotonic() - t_start, 1)
            return (
                "data: " + json.dumps({
                    "thinking":  True,
                    "elapsed_s": elapsed,
                    "activity":  activity["label"],
                }) + "\n\n"
            )

        # First tick immediately so the UI swaps "…" → "0.0s" instantly.
        yield _emit_tick()

        # Default to the user's current/last project — if none we can't
        # actually queue, so degrade gracefully with a clear message.
        project_id = body.project_id or ""
        if not project_id or project_id == "home":
            content = (
                "🚢 Ship-shortcut detected, but no project is selected. "
                "Open a project in the sidebar and run **ship** again."
            )
            for i in range(0, len(content), 16):
                yield f"data: {json.dumps({'token': content[i:i+16]})}\n\n"
            yield f"data: {json.dumps({'done': True, 'session_id': body.session_id, 'provider': 'aurem-ship-shortcut', 'verified_paths': []})}\n\n"
            return

        # Run the heavy work (DB fetch + GitHub validation + enqueue) in a
        # background task while we yield tick frames every 0.5 s.
        async def _do_enqueue():
            try:
                from routers.cto_projects import _enqueue_cto_task
                return ("ok", await _enqueue_cto_task(
                    user_id=user_id, project_id=project_id, task_text=brief,
                ))
            except Exception as e:
                return ("error", e)

        enqueue_t = asyncio.create_task(_do_enqueue())
        # Iter 136 — hard ceiling on the enqueue so a hung GitHub /
        # Mongo call can never strand the user on "thinking…" forever.
        # 60 s is conservative — a healthy enqueue completes in <3 s.
        _SHIP_ENQUEUE_TIMEOUT_S = float(os.getenv("SHIP_ENQUEUE_TIMEOUT_S", "60"))
        _ship_start = _t.monotonic()
        try:
            while not enqueue_t.done():
                try:
                    await asyncio.wait_for(asyncio.shield(enqueue_t), timeout=0.5)
                except asyncio.TimeoutError:
                    if _t.monotonic() - _ship_start > _SHIP_ENQUEUE_TIMEOUT_S:
                        enqueue_t.cancel()
                        content = (
                            f"🚢 Ship-shortcut timed out after "
                            f"{int(_SHIP_ENQUEUE_TIMEOUT_S)}s — GitHub / Mongo "
                            "did not respond. Please retry the prompt."
                        )
                        for i in range(0, len(content), 16):
                            yield f"data: {json.dumps({'token': content[i:i+16]})}\n\n"
                        yield (
                            "data: " + json.dumps({
                                "done": True,
                                "session_id": body.session_id,
                                "provider": "aurem-ship-shortcut",
                                "verified_paths": [],
                                "timed_out": True,
                            }) + "\n\n"
                        )
                        return
                    yield _emit_tick()
            kind, payload = enqueue_t.result()
        finally:
            stop_event.set()

        if kind == "error":
            e = payload
            content = (
                f"🚢 Ship-shortcut failed to queue: {type(e).__name__}: {e}. "
                "Try again, or run the task from a fresh prompt."
            )
            for i in range(0, len(content), 16):
                yield f"data: {json.dumps({'token': content[i:i+16]})}\n\n"
            yield f"data: {json.dumps({'done': True, 'session_id': body.session_id, 'provider': 'aurem-ship-shortcut', 'verified_paths': []})}\n\n"
            return

        res = payload
        if not res.get("ok"):
            reason = res.get("reason", "unknown")
            content = (
                f"🚢 Ship-shortcut blocked: **{reason}**. "
                "Connect a GitHub repo (Settings → GitHub) and retry."
            )
            for i in range(0, len(content), 16):
                yield f"data: {json.dumps({'token': content[i:i+16]})}\n\n"
            yield f"data: {json.dumps({'done': True, 'session_id': body.session_id, 'provider': 'aurem-ship-shortcut', 'verified_paths': []})}\n\n"
            return

        task_id = res["task_id"]
        # Iter 125 — emit the same `task_handoff` SSE frame the Mode D→C
        # path uses so the floating LiveTaskPopup mounts immediately. The
        # shortcut path previously only stuffed task_id in the `done`
        # payload, which `onDone` doesn't read — so the popup never
        # appeared on ship shortcuts (the most common Mode C trigger).
        yield (
            "data: " + json.dumps({
                "type": "task_handoff",
                "task_id": task_id,
                "project_id": res.get("project_id") or project_id,
                "source": "ship_shortcut",
            }) + "\n\n"
        )
        content = (
            f"🚢 **Shipped via shortcut** — task `{task_id}` queued from the "
            f"previous handoff brief. The worker will commit directly to "
            f"your repo; live progress is in the task tape below."
        )
        # Mark the task so the UI knows it came from a shortcut.
        try:
            await db.cto_tasks.update_one(
                {"task_id": task_id},
                {"$set": {"source": "chat_ship_shortcut"}},
            )
        except Exception:
            pass
        for i in range(0, len(content), 16):
            yield f"data: {json.dumps({'token': content[i:i+16]})}\n\n"
        done_payload = {
            "done": True,
            "provider": "aurem-ship-shortcut",
            "session_id": body.session_id,
            "verified_paths": [],
            "ship_shortcut": True,
            "task_id": task_id,
        }
        yield f"data: {json.dumps(done_payload)}\n\n"

    return _stream()


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatBody,
    authorization: Optional[str] = Header(None),
):
    """SSE token-streaming chat. Iter 45: rate-limited to 30 req/min per IP.
    Iter 50.1: founders / unlimited accounts bypass the rate-limit."""
    user = await current_dev(authorization)
    if not (bool(user.get("is_unlimited")) or user.get("tier") == "founder"):
        from services.rate_limiter import check_rate_limit, client_ip_from_request
        if not check_rate_limit(f"chat:{client_ip_from_request(request)}", 30):
            raise HTTPException(429, "Rate limit exceeded: 30 chats/min/IP")
    jwt_token = authorization.split(" ", 1)[1] if authorization else ""
    user_id = user.get("user_id", "")

    # Iter 38: ORA is founder-only. The ORA API key is shared across all
    # founders, so we gate at the surface to avoid customer quota burn.
    if (body.agent or "").lower() == "ora":
        from services.usage import is_founder_email
        if not is_founder_email(user.get("email")):
            raise HTTPException(403, "ORA agent is founder-only")

    # Iter 157 — COLD START FIX.
    # Three context-builders below used to run sequentially with NO
    # outer timeout:
    #   - get_repo_context()  → 5-15 GitHub API calls (worst case 15-45s)
    #   - get_brain_context() → Mongo read + optional GH PAT call (1-5s)
    #   - build_url_context() → external URL scrape (1-10s)
    #
    # On a fresh chat session against a real repo the wall-clock for
    # JUST the context build was hitting 30-60s BEFORE the LLM was
    # even invoked, which fed the 300s "thinking…" stalls users were
    # reporting on production.
    #
    # Fix:
    #   1. Run all three IN PARALLEL via asyncio.gather.
    #   2. Wrap each in asyncio.wait_for(timeout=12s). If a builder
    #      misses the budget we degrade with an empty string — the
    #      orchestrator still has the persona + local tools and the
    #      LLM can call read_repo_file itself.
    #   3. Total upper bound for the context phase: 12s (not 60s+).
    async def _safe(coro, label, timeout_s=12.0):
        try:
            return await asyncio.wait_for(coro, timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning(f"chat-context: {label} timed out after {timeout_s}s — degrading")
            return ""
        except Exception as e:
            logger.warning(f"chat-context: {label} failed ({e!r}) — degrading")
            return ""

    repo_ctx, url_ctx = await asyncio.gather(
        _safe(get_repo_context(user_id, body.project_id or ""), "repo_context"),
        _safe(build_url_context(body.prompt), "url_context"),
    )

    # ── Iter 87: "ship" shortcut ──────────────────────────────────────
    # When the user's prompt is just "ship" / "do it" / "go" right after
    # an assistant turn that already emitted an ```aurem-handoff fence,
    # the model SHOULD NOT re-run the whole reasoning loop. That's the
    # bug we kept seeing on auremcto.com: 10 tool calls / 90 s budget /
    # timeout / no progress. Instead, lift the brief from the prior
    # turn and queue the cto_task directly.
    shipped_via_shortcut = await _maybe_ship_shortcut(
        body=body, user_id=user_id, repo_ctx=repo_ctx,
    )
    if shipped_via_shortcut is not None:
        return StreamingResponse(
            shipped_via_shortcut, media_type="text/event-stream",
        )
    # Inject the project's persistent memory (recent commits, tech stack,
    # past decisions, rejected ideas, recurring bugs) so a fresh chat
    # turn knows what AUREM has already shipped on this repo. Previously
    # only the CTO worker used the brain — chat never read it, which is
    # why users kept getting "I don't know about that feature" replies
    # for things AUREM itself had committed minutes earlier.
    brain_ctx = ""
    if body.project_id and body.project_id != "home":
        try:
            _proj = await get_db().cto_projects.find_one(
                {"project_id": body.project_id, "user_id": user_id},
                {"_id": 0, "github_owner": 1, "github_repo": 1},
            )
            owner = (_proj or {}).get("github_owner") or ""
            repo = (_proj or {}).get("github_repo") or ""
            repo_full = f"{owner}/{repo}" if owner and repo else body.project_id
            from services.project_brain import get_brain_context
            # Best-effort: surface the GitHub PAT so the brain can pull
            # the last 5 commits from the remote — covers commits made
            # outside AUREM (direct CLI pushes / other contributors).
            _pat = None
            try:
                from routers.cto_projects import _decrypt_pat, _user_gh_token
                _pat = await _decrypt_pat(user_id, (_proj or {}).get("github_token")) \
                    or await _user_gh_token(user_id)
            except Exception:
                _pat = None
            # Iter 157 — also wrap brain context in the same 12s budget;
            # this used to be the slowest of the three on first turn
            # because it pulls remote commit history.
            brain_ctx = await _safe(
                get_brain_context(
                    get_db(), body.project_id, repo_full,
                    github_token=_pat,
                ),
                "brain_context",
            )
            if brain_ctx:
                brain_ctx = "[PROJECT MEMORY]\n" + brain_ctx
        except Exception:
            logger.exception("chat: brain context fetch failed (continuing)")
            brain_ctx = ""
    extra_sys = "\n\n".join(
        s for s in (repo_ctx, brain_ctx, url_ctx) if s
    )

    # Iter 159 — ASK ORA panel uses a deliberately CASUAL voice.
    # This block is injected ONLY when the caller sets ora_panel=true
    # (the floating right-side panel). The main coding chat is
    # untouched — it keeps the professional `AUREM_CTO_PERSONA` tone
    # from orchestrator.py. The block goes LAST in extra_sys so it
    # overrides the default TONE & FORMAT layer for this turn only.
    # Iter 160 — tightened for TTS playback: ZERO emoji, ZERO em-dash,
    # ZERO symbol decoration. SpeechSynthesis reads symbols literally
    # ("emoji man waving"), so the voice override now demands plain
    # words only.
    if body.ora_panel:
        _ora_voice = (
            "# ASK-ORA VOICE OVERRIDE — this turn only\n"
            "You are answering through the ASK ORA side panel, not\n"
            "the main coding chat. The reply will be read aloud by\n"
            "the user's text-to-speech, so use PLAIN WORDS ONLY:\n"
            "  - No emoji of any kind. None.\n"
            "  - No em-dash, en-dash, arrows, asterisks or bullet\n"
            "    decorations in prose. Plain sentences with periods\n"
            "    and commas only.\n"
            "  - Code fences are still allowed when sharing code.\n"
            "  - Short sentences. Warm and friendly but never robotic.\n"
            "\n"
            "## Banned phrases (zero exceptions)\n"
            "  'Certainly!', 'Of course!', 'Absolutely!',\n"
            "  'Great question!', 'As an AI', 'I have analyzed',\n"
            "  'I have identified', 'I have reviewed',\n"
            "  'I have successfully', 'Please note', 'I would like to',\n"
            "  'I will proceed to', 'Comprehensive solution',\n"
            "  'Let me know if you have any questions!'\n"
            "\n"
            "## Bad vs good — mirror these patterns (plain text)\n"
            "  BAD : 'I have reviewed your codebase and identified\n"
            "         several issues.'\n"
            "  GOOD: 'Looked through it. Found three things, fixing\n"
            "         the big one first.'\n"
            "\n"
            "  BAD : 'Certainly! I will now proceed to fix the auth\n"
            "         module.'\n"
            "  GOOD: 'On it. Auth fix coming up.'\n"
            "\n"
            "  BAD : 'I have successfully committed the changes.'\n"
            "  GOOD: 'Shipped. Check your repo.'\n"
            "\n"
            "  BAD : 'Please provide more information about the\n"
            "         issue.'\n"
            "  GOOD: 'Tell me more. What exactly is breaking?'\n"
            "\n"
            "  BAD : 'I will analyze the error and provide a\n"
            "         comprehensive solution.'\n"
            "  GOOD: 'Got it. Give me a sec to dig in.'\n"
            "\n"
            "  BAD : 'Great question! As an AI, I can help you\n"
            "         understand.'\n"
            "  GOOD: 'Short answer first, then the details.'\n"
            "\n"
            "## Energy by situation\n"
            "  Quick task: snappy. One line if one line works.\n"
            "  Complex: focused, calm, confident. Outline the plan,\n"
            "    then execute step by step.\n"
            "  Error: honest, solution first. State what you know\n"
            "    and what you will try.\n"
            "  Win: share the moment briefly. 'Done.' or 'Shipped,\n"
            "    commit such-and-such.'\n"
            "\n"
            "## Hard rules that survive the casual tone\n"
            "  - All hallucination and honesty rules still apply.\n"
            "  - Code stays inside fenced blocks (``` ... ```). Always.\n"
            "  - Never close with 'Let me know if you have questions'.\n"
            "  - Never write essays when one line works.\n"
            "  - Never sycophantic openers.\n"
        )
        extra_sys = (extra_sys + "\n\n" + _ora_voice).strip()

    async def gen():
        import time as _t
        t_start = _t.monotonic()
        # Iter 36: hard wall-clock ceiling — if the worker doesn't return
        # within HARD_TIMEOUT_S we abort and emit a friendly error so the
        # UI can never "thinking…" for 15 minutes again.
        # ── Wall-clock timeout. Was a flat 90 s — too tight for users
        # working on larger repos where a single GitHub read costs 3-8 s
        # on cold cache and the LLM's first response can hit 15-20 s on
        # OpenRouter cold-start. Bumped to a 150 s default and made it
        # env-configurable so prod can tune without a redeploy.
        # Pattern #2 in RECURRING_ISSUES.md: the previous 90 s budget
        # was getting eaten by the first tool call on real user repos,
        # then "do it" on the retry hit the same wall.
        # Iter 160 — was 150s, tightened to 90s. With orchestrator
        # per-turn budget at 75s and LLM call cap at 25s × 2 retries,
        # a 90s wall-clock ceiling guarantees the user never sees a
        # spinner past 1.5 min regardless of upstream behaviour.
        HARD_TIMEOUT_S = float(os.getenv("CHAT_HARD_TIMEOUT_S", "90"))
        stop_event = asyncio.Event()
        q: asyncio.Queue = asyncio.Queue()
        # Shared activity hint the worker mutates as it progresses; the
        # ticker copies it into every tick frame.
        activity = {"label": "thinking…"}

        async def _ticker():
            while True:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.6)
                    return
                except asyncio.TimeoutError:
                    elapsed = round(_t.monotonic() - t_start, 1)
                    # Iter 149 — also emit the LIVE tool invocations list so
                    # the UI can render chips ("read_repo_file ✓", "search_repo …")
                    # right below the thinking bar instead of only the label.
                    _inv = list(activity.get("invocations") or [])
                    await q.put({
                        "type": "tick",
                        "elapsed_s": elapsed,
                        "activity": activity["label"],
                        "invocations": _inv,
                    })

        async def _worker():
            try:
                # ─── Iter 42 — Mode D fix-confirmation fast path ─────────
                # If the user previously got a Mode D diagnosis with an
                # auto-fixable issue, we stashed `pending_fix_task` on the
                # chat session. A short "yes / fix it / ship it" reply
                # triggers a Mode C task using that stored description.
                if body.session_id and is_fix_confirmation(body.prompt or ""):
                    _db = get_db()
                    if _db is not None:
                        _sess = await _db.chat_sessions.find_one(
                            {"session_id": body.session_id},
                            {"_id": 0, "pending_fix_task": 1, "user_id": 1},
                        )
                        _pending = (_sess or {}).get("pending_fix_task") if _sess else None
                        if _pending and (not _sess.get("user_id") or _sess.get("user_id") == user_id):
                            # Clear the pending flag so a stray "yes" later
                            # doesn't accidentally fire another task.
                            await _db.chat_sessions.update_one(
                                {"session_id": body.session_id},
                                {"$unset": {"pending_fix_task": ""}},
                            )
                            await q.put({"type": "mode", "mode": "C"})
                            # Iter 46 — actually enqueue a real Mode C task
                            # (previously this only emitted a friendly reply
                            # with the task description; no real cto_tasks
                            # row was created).
                            from routers.cto_projects import _enqueue_cto_task
                            enq = await _enqueue_cto_task(
                                user_id=user_id,
                                project_id=body.project_id,
                                task_text=_pending,
                                bg=None,
                                maxx_mode=body.maxx_mode,
                            )
                            # Iter 48 — Sentry: this is the exact code path
                            # that silently failed in production (friendly
                            # reply, no real task). Capture every outcome.
                            try:
                                import sentry_sdk
                                sentry_sdk.add_breadcrumb(
                                    category="mode_handoff",
                                    message="Mode D → C handoff fired",
                                    level="info",
                                    data={
                                        "ok": enq.get("ok"),
                                        "reason": enq.get("reason"),
                                        "task_id": enq.get("task_id"),
                                        "project_id": enq.get("project_id"),
                                    },
                                )
                                if not enq.get("ok"):
                                    sentry_sdk.capture_message(
                                        f"Mode D→C handoff failed: {enq.get('reason', 'unknown')}",
                                        level="error",
                                    )
                            except Exception:
                                pass
                            if enq.get("ok"):
                                reply = (
                                    "On it — Mode C task **queued** and the "
                                    f"agent is starting now.\n\n"
                                    f"_Task:_ {_pending}\n"
                                    f"_Project:_ `{enq.get('project_id')}`  "
                                    f"_Task ID:_ `{enq.get('task_id')}`\n\n"
                                    "I'll commit the fix automatically. "
                                    "Open the task list to watch progress."
                                )
                            elif enq.get("reason") == "no_project":
                                reply = (
                                    "I diagnosed the issue, but you don't "
                                    "have a connected GitHub project yet. "
                                    "Add one from the dashboard and I'll "
                                    "ship the fix immediately."
                                )
                            elif enq.get("reason") == "no_pat":
                                reply = (
                                    "The diagnosis is ready, but I don't "
                                    "have a working GitHub token for this "
                                    "project. Reconnect it from the "
                                    "dashboard and re-run."
                                )
                            else:
                                reply = (
                                    "Couldn't enqueue the fix right now "
                                    f"({enq.get('reason', 'unknown')}). "
                                    "Try again in a moment."
                                )
                            result = {
                                "ok": True, "content": reply,
                                "provider": "mode-d-handoff",
                                "fallback_chain": ["mode_d_handoff"],
                                "iterations": 1, "tool_calls_run": 0,
                                "tool_invocations": [],
                                "mode": "C",
                                "pending_fix_handed_off": enq.get("ok", False),
                                "fix_task": _pending,
                                "task_id": enq.get("task_id"),
                                "project_id": enq.get("project_id"),
                            }
                            await q.put({"type": "result", "result": result})
                            return

                # Decide A/B/C/D/E/F once and broadcast to frontend so the UI
                # can show the live pill before tokens stream.
                _mode = classify_intent(body.prompt or "", body.f12_payload)
                # Confidence scoring — surfaces a `mode_confirm` event when
                # the message is ambiguous so the UI can ask the user
                # before burning an LLM call on the wrong mode. Honoured
                # only when the user has NOT explicitly overridden via
                # body.mode_override (mode_override skips confirm).
                try:
                    from services.mode_classifier import classify_intent_v2
                    _conf = classify_intent_v2(body.prompt or "", body.f12_payload)
                except Exception:
                    _conf = None
                if _conf:
                    await q.put({
                        "type": "mode",
                        "mode": _mode,
                        "confidence": _conf["confidence"],
                        "scores": _conf["scores"],
                        "needs_confirm": _conf["needs_confirm"]
                            and not getattr(body, "mode_override", None),
                    })
                    # Fire-and-forget telemetry — keeps a rolling window
                    # of the last 100 classifications so we can tune the
                    # vocabulary against real-world ambiguity. Failures
                    # MUST NOT block the chat path.
                    try:
                        from services.mode_classifier import log_classification
                        _ = asyncio.create_task(
                            log_classification(get_db(), _conf, body.prompt or "")
                        )
                    except Exception:
                        pass
                else:
                    await q.put({"type": "mode", "mode": _mode})

                # Ops-intent signal — surfaces a deep-link to /admin/ops
                # when the user asks for a server operation AUREM can't
                # execute on their infra (e.g. "restart supervisor",
                # "free disk space"). Avoids ORA fabricating bash.
                try:
                    from services.mode_classifier import looks_like_ops_request
                    if looks_like_ops_request(body.prompt or ""):
                        await q.put({"type": "ops_redirect",
                                     "url": "/admin/ops",
                                     "reason": "This is a server operation. "
                                               "AUREM can't run commands on "
                                               "your infrastructure — open "
                                               "the Ops Recipes for copy-paste "
                                               "runbooks."})
                except Exception:
                    pass

                # Mode D — debug session (READ → DIAGNOSE → CONFIRM → fix)
                # Mode E — full repo audit (REPORT only, no commit)
                if _mode in ("D", "E"):
                    from services.mode_d_debugger import run_debug_session
                    from services.mode_e_auditor  import run_audit
                    from routers.cto_projects     import _user_gh_token

                    db_h     = get_db()
                    repo_own = ""
                    repo_nm  = ""
                    branch_h = "main"
                    project  = None
                    if db_h is not None and body.project_id and body.project_id != "home":
                        project = await db_h.cto_projects.find_one(
                            {"project_id": body.project_id, "user_id": user_id}
                        )
                        if project:
                            repo_own = project.get("github_owner", "")
                            repo_nm  = project.get("github_repo", "")
                            branch_h = project.get("branch", "main")
                    pat = None
                    try:
                        pat = (project or {}).get("github_token") or await _user_gh_token(user_id)
                    except Exception:
                        pat = None

                    if _mode == "D":
                        activity["label"] = "diagnosing error…"
                        try:
                            d_result = await run_debug_session(
                                db=db_h,
                                user_message=body.prompt or "",
                                repo_owner=repo_own,
                                repo_name=repo_nm,
                                repo_ctx=f"{repo_own}/{repo_nm}" if repo_own else "no-repo",
                                user_id=user_id,
                                project_id=body.project_id,
                                f12_payload=body.f12_payload,
                                github_pat=pat,
                            )
                        except Exception as _de:
                            d_result = {
                                "ora_reply": f"Couldn't diagnose: {_de}",
                                "can_auto_fix": False, "commit_task": "",
                                "severity": "unknown", "fast_path_used": False,
                            }
                        # Persist pending fix (so a "yes fix it" reply triggers Mode C)
                        if d_result.get("can_auto_fix") and body.session_id and db_h is not None:
                            try:
                                await db_h.chat_sessions.update_one(
                                    {"session_id": body.session_id},
                                    {"$set": {"pending_fix_task": d_result["commit_task"],
                                              "pending_fix_set_at": time.time()}},
                                    upsert=True,
                                )
                            except Exception:
                                pass
                        result = {
                            "ok": True,
                            "content":  d_result.get("ora_reply", ""),
                            "provider": "mode-d-debugger",
                            "fallback_chain": ["mode_d"],
                            "iterations": 1, "tool_calls_run": 0,
                            "tool_invocations": [], "mode": "D",
                            "can_auto_fix": d_result.get("can_auto_fix", False),
                            "severity": d_result.get("severity", "medium"),
                            "fast_path_used": d_result.get("fast_path_used", False),
                        }
                        await q.put({"type": "result", "result": result})
                        return

                    # Mode E — audit
                    activity["label"] = "scanning repo…"
                    file_blocks: dict = {}
                    file_tree:   list = []
                    if pat and repo_own and repo_nm:
                        try:
                            from services.github_api_writer import fetch_file as _gh_fetch
                            import httpx as _httpx
                            # Pull file tree directly from the git tree endpoint
                            # (one round-trip — much lighter than full repo_context).
                            async with _httpx.AsyncClient(timeout=20.0) as _gc:
                                _r = await _gc.get(
                                    f"https://api.github.com/repos/{repo_own}/{repo_nm}/git/trees/{branch_h}?recursive=1",
                                    headers={"Authorization": f"Bearer {pat}",
                                             "Accept": "application/vnd.github+json"},
                                )
                                if _r.status_code == 200:
                                    _tree = (_r.json() or {}).get("tree", []) or []
                                    file_tree = [
                                        t.get("path", "")
                                        for t in _tree
                                        if t.get("type") == "blob" and t.get("path")
                                    ][:400]
                            # Read the top ~8 most-relevant files for the audit
                            _prio = [
                                p for p in file_tree
                                if any(p.endswith(ext) for ext in
                                       (".py", ".js", ".jsx", ".ts", ".tsx"))
                                and ("router" in p or "service" in p
                                     or "model" in p or "main" in p
                                     or "App" in p or "index" in p)
                            ][:8] or file_tree[:8]
                            async with _httpx.AsyncClient(timeout=20.0) as _gc:
                                for _p in _prio:
                                    _content = await _gh_fetch(
                                        _gc, repo_own, repo_nm, _p, branch_h, pat,
                                    )
                                    if _content:
                                        file_blocks[_p] = _content
                        except Exception:
                            pass
                    try:
                        e_result = await run_audit(
                            db=db_h,
                            repo_ctx=f"{repo_own}/{repo_nm}" if repo_own else "no-repo",
                            file_blocks=file_blocks,
                            file_tree=file_tree,
                            user_message=body.prompt or "",
                            user_id=user_id,
                            project_id=body.project_id,
                        )
                    except Exception as _ee:
                        e_result = {"report": f"Couldn't audit: {_ee}",
                                    "critical_count": 0, "high_count": 0,
                                    "fixable_tasks": []}
                    result = {
                        "ok": True,
                        "content":  e_result.get("report", ""),
                        "provider": "mode-e-auditor",
                        "fallback_chain": ["mode_e"],
                        "iterations": 1, "tool_calls_run": 0,
                        "tool_invocations": [], "mode": "E",
                        "critical_count": e_result.get("critical_count", 0),
                        "high_count":     e_result.get("high_count", 0),
                        "fixable_tasks":  e_result.get("fixable_tasks", []),
                    }
                    await q.put({"type": "result", "result": result})
                    return

                # Iter 81 — Mode B auto-upgrade: Decision Council. Only
                # fires when classifier picked Mode B AND the message
                # has genuine stuck-decision signals. Regular Mode B
                # advice (e.g. "should I add caching") falls through to
                # the orchestrator below.
                if _mode == "B":
                    from services.mode_b_council import is_council_request, run_council
                    if is_council_request(body.prompt or "", _mode):
                        activity["label"] = "convening the council…"
                        try:
                            council_md = await run_council(
                                prompt=body.prompt or "",
                                repo_ctx=repo_ctx or "",
                                brain_ctx=brain_ctx or "",
                            )
                        except Exception as _ce:
                            logger.exception("mode B council failed")
                            council_md = (
                                f"_(Council failed: {_ce}. Try again or "
                                "rephrase the decision more concretely.)_"
                            )
                        result = {
                            "ok": True,
                            "content":  council_md,
                            "provider": "mode-b-council",
                            "fallback_chain": ["mode_b_council"],
                            "iterations": 1, "tool_calls_run": 0,
                            "tool_invocations": [], "mode": "B",
                            "council": True,
                        }
                        await q.put({"type": "result", "result": result})
                        return

                # Iter 60 — Mode F (Engage / Market). Token-cheap single
                # LLM call routed through mode_f_engage. We pass the
                # already-built repo + brain context so the LLM can
                # ground market advice in what the user is actually
                # shipping. No tool loop, no max-iters budget burn.
                if _mode == "F":
                    activity["label"] = "thinking about positioning…"
                    from services.mode_f_engage import run_engage
                    try:
                        engage_content = await run_engage(
                            prompt=body.prompt or "",
                            repo_ctx=repo_ctx or "",
                            brain_ctx=brain_ctx or "",
                        )
                    except Exception as _fe:
                        logger.exception("mode F engage failed")
                        engage_content = (
                            f"_(Engage mode failed: {_fe}. Try again, or "
                            "ask the question more directly.)_"
                        )
                    result = {
                        "ok": True,
                        "content":  engage_content,
                        "provider": "mode-f-engage",
                        "fallback_chain": ["mode_f"],
                        "iterations": 1, "tool_calls_run": 0,
                        "tool_invocations": [], "mode": "F",
                    }
                    await q.put({"type": "result", "result": result})
                    return

                # Iter 38: ORA branch. Founder-only — checked at the
                # endpoint surface below. Skips orchestrator + tools
                # entirely; calls aurem.live's hosted ORA model.
                if (body.agent or "auto").lower() == "ora":
                    from services.ora_client import call_ora
                    from fastapi import HTTPException as _HTTPExc
                    activity["label"] = "calling ORA on aurem.live…"
                    # ORA is aurem.live's hosted brain — it has its own
                    # context system. We MUST NOT dump our local repo tree
                    # (it's huge, and upstream caps system_hint at 400 chars
                    # → 422). Send only a tiny scope hint instead.
                    ora_hint = None
                    try:
                        if body.project_id and body.project_id != "home":
                            _proj = await get_db().cto_projects.find_one(
                                {"project_id": body.project_id, "user_id": user_id}
                            )
                            if _proj:
                                owner = _proj.get("github_owner", "")
                                repo  = _proj.get("github_repo", "")
                                br    = _proj.get("branch", "main")
                                if owner and repo:
                                    ora_hint = f"User is scoped to repo {owner}/{repo}@{br}."[:380]
                    except Exception:
                        ora_hint = None
                    # Graceful upstream failure: if aurem.live errors (their
                    # own LLM 500, 429, 504, etc.), DO NOT crash the SSE
                    # stream — auto-fall back to the local AUREM orchestrator
                    # so the user always gets a real answer. The error is
                    # logged as INFO (not ERROR) so production logs aren't
                    # spammed with upstream issues out of our control.
                    try:
                        resp = await call_ora(
                            message=body.prompt,
                            session_id=body.session_id,
                            system_hint=ora_hint,
                        )
                        result = {
                            "ok":       bool(resp.get("ok", True)),
                            "content":  resp.get("reply") or "",
                            "provider": f"ora-{resp.get('model','?')}",
                            "fallback_chain": ["ora"],
                            "iterations": 1,
                            "tool_calls_run": 0,
                            "tool_invocations": [],
                            "mode": "ora",
                        }
                        await q.put({"type": "result", "result": result})
                        return
                    except _HTTPExc as ora_err:
                        # Iter 107 — log the FIRST trip at INFO, but once
                        # the circuit-breaker is open (status 503 from
                        # ora_client without an HTTP call), drop to DEBUG
                        # to silence production log spam.
                        _ora_status = getattr(ora_err, "status_code", 0)
                        _ora_detail = str(getattr(ora_err, "detail", "")).lower()
                        if _ora_status == 503 and "circuit" in _ora_detail:
                            logger.debug("ora upstream circuit-open — using AUREM")
                        else:
                            logger.info(
                                "ora upstream unavailable (%s) — falling back to AUREM",
                                _ora_status or "?",
                            )
                        activity["label"] = "ORA unavailable — switching to AUREM CTO…"
                        # Fall through to the AUREM/orchestrator path below.

                activity["label"] = "thinking…"
                # Iter 153 — clamp mode to tier-allowed set for this stream.
                from services.subscription_tiers import allowed_modes_for_tier as _allowed_modes
                _allowed_s = _allowed_modes((user or {}).get("tier") or "free")
                req_mode_stream = body.mode if (body.mode in _allowed_s) else _allowed_s[-1]
                # Hook to publish tool invocations live so the timeout
                # guard can summarise what we managed to inspect.
                _published: list[dict] = []
                activity["invocations"] = _published
                _orig_activity_hook = activity.__setitem__
                def _activity(label: str):
                    activity["label"] = label
                result = await chat_with_tools(
                    prompt=body.prompt,
                    jwt_token=jwt_token,
                    system=(extra_sys + "\n\n" if extra_sys else None),
                    max_iters=min(max(body.max_tool_iters, 4), 6),
                    session_id=body.session_id,
                    mongo_client=None,
                    user_id=user_id,
                    project_id=body.project_id,
                    activity_hook=_activity,
                    live_invocations_ref=_published,
                    mode=req_mode_stream,
                )
                # Snapshot final invocations so a late timeout still has data.
                if isinstance(result, dict):
                    _published[:] = result.get("tool_invocations") or []
                await q.put({"type": "result", "result": result})
            except Exception as e:
                logger.exception("chat_stream orchestrator failed")
                await q.put({"type": "error", "error": str(e)})
            finally:
                stop_event.set()

        ticker_t = asyncio.create_task(_ticker())
        worker_t = asyncio.create_task(_worker())

        # Iter 141 — emit an immediate meta frame so the client gets
        # progress feedback inside 10 ms instead of waiting for the
        # orchestrator's first LLM round-trip (which can be 1-5 s on
        # OpenRouter cold-start). The frontend uses this to anchor the
        # real-progress bar at 15% the moment the request is accepted.
        yield (
            "data: " + json.dumps({
                "meta": True,
                "session_id": body.session_id,
                "provider": "aurem-cto",
                "thinking_s": 0.0,
                "tool_calls_run": 0,
            }) + "\n\n"
        )

        result = None
        deadline_at = _t.monotonic() + HARD_TIMEOUT_S
        while True:
            try:
                ev = await asyncio.wait_for(
                    q.get(), timeout=max(0.1, deadline_at - _t.monotonic()),
                )
            except asyncio.TimeoutError:
                ev = None  # synthetic timeout — handled below
            # Iter 136 — explicit deadline check.
            # The `_ticker()` task fires every 0.6s and feeds the queue, so
            # wait_for(q.get(), ...) almost always returns before the
            # configured timeout. Result: HARD_TIMEOUT_S was never being
            # enforced — users saw "thinking · 500s" past the 150s budget.
            # Now we treat ANY tick that arrives past the deadline as a
            # timeout, but DON'T throw away a real `result` / `mode` / `error`
            # event just because it raced past the cut-off by a few ms.
            _past_deadline = _t.monotonic() >= deadline_at
            _is_tick = isinstance(ev, dict) and ev.get("type") == "tick"
            if ev is None or (_past_deadline and _is_tick):
                # Wall-clock blown. Cancel everything but emit a USEFUL
                # message instead of just an "error" payload — the
                # frontend used to render that red and the user saw
                # nothing actionable. We pull whatever tool history the
                # worker managed to record and stream a real summary.
                worker_t.cancel()
                ticker_t.cancel()
                partial_invocations = list(activity.get("invocations") or [])
                from services.orchestrator import _synthesise_max_iters_summary
                summary = _synthesise_max_iters_summary(
                    body.prompt, partial_invocations,
                )
                # RECURRING_ISSUES.md Pattern #2 fix: distinguish slow-API
                # waiting from a genuine reasoning loop. If we made very
                # few tool calls, the time was likely spent waiting on
                # the model API (cold start / OpenRouter queue / network),
                # NOT looping. Telling the user "I cut myself off" in
                # that case is misleading and erodes trust.
                tool_count = len(partial_invocations)
                if tool_count < 3:
                    content = (
                        f"⏱️ Model API was slow to respond — waited "
                        f"{int(HARD_TIMEOUT_S)}s and only got "
                        f"{tool_count} tool call{'s' if tool_count != 1 else ''} "
                        f"through. This usually means OpenRouter/DeepSeek "
                        f"cold-started or a network blip — NOT that I was "
                        f"stuck in a loop. Please retry the same prompt.\n\n"
                        f"{summary}"
                    )
                else:
                    content = (
                        f"⏱️ I cut myself off at {int(HARD_TIMEOUT_S)}s to avoid "
                        f"a runaway tool-loop.\n\n{summary}"
                    )
                # Stream as a normal assistant turn (meta → tokens → done)
                # so the bubble renders properly instead of going red.
                meta_payload = {
                    "meta": True,
                    "session_id": body.session_id,
                    "provider": "aurem-timeout-guard",
                    "mode": "A",
                    "temperature": 0.2,
                    "thinking_s": round(_t.monotonic() - t_start, 1),
                    "tool_calls_run": len(partial_invocations),
                    "timed_out": True,
                    "slow_api": tool_count < 3,
                }
                yield f"data: {json.dumps(meta_payload)}\n\n"
                CHUNK = 16
                for i in range(0, len(content), CHUNK):
                    yield f"data: {json.dumps({'token': content[i:i+CHUNK]})}\n\n"
                    await asyncio.sleep(0.005)
                # Persist the turn so refresh keeps it visible.
                try:
                    await _persist_turn(
                        user_id, body.session_id or "",
                        body.prompt, content, "aurem-timeout-guard",
                        project_id=body.project_id,
                    )
                except Exception:
                    logger.exception("timeout persist_turn failed")
                yield (
                    "data: " + json.dumps({
                        "done": True,
                        "provider": "aurem-timeout-guard",
                        "session_id": body.session_id,
                        "tokens_remaining": None,
                        "timed_out": True,
                    }) + "\n\n"
                )
                return
            if ev["type"] == "tick":
                yield (
                    "data: " + json.dumps({
                        "thinking":    True,
                        "elapsed_s":   ev["elapsed_s"],
                        "activity":    ev["activity"],
                        "invocations": ev.get("invocations") or [],
                    }) + "\n\n"
                )
            elif ev["type"] == "mode":
                # Iter 42 — forward classified mode (A/B/C/D/E) to UI so
                # the pill renders BEFORE tokens stream.
                yield f"data: {json.dumps({'type': 'mode', 'mode': ev['mode']})}\n\n"
            elif ev["type"] == "error":
                yield f"data: {json.dumps({'error': ev['error']})}\n\n"
                return
            elif ev["type"] == "result":
                result = ev["result"]
                break

        # Iter 51 — SSE Task Progress Streamer.
        # When the worker auto-enqueued a Mode C task (Mode D→C handoff,
        # or any future flow that lands a `task_id` on the result), surface
        # it to the frontend BEFORE meta/content streaming so the chat
        # bubble can pin the live ShipStatusCard without waiting for the
        # full text reply.
        handoff_task_id = result.get("task_id") if isinstance(result, dict) else None
        handoff_project_id = result.get("project_id") if isinstance(result, dict) else None
        if handoff_task_id:
            yield (
                "data: " + json.dumps({
                    "type": "task_handoff",
                    "task_id": handoff_task_id,
                    "project_id": handoff_project_id,
                    "source": result.get("provider") or "auto_handoff",
                }) + "\n\n"
            )

        content = result.get("content", "") or ""
        provider = result.get("provider", "") or ""
        mode = _detect_mode(body.prompt)
        from services.llm import temperature_for
        temperature = temperature_for(mode)

        meta = {"meta": True, "session_id": body.session_id,
                "provider": provider, "mode": mode, "temperature": temperature,
                "thinking_s": round(_t.monotonic() - t_start, 1),
                "tool_calls_run": result.get("tool_calls_run", 0)}
        yield f"data: {json.dumps(meta)}\n\n"

        CHUNK = 6
        i = 0
        while i < len(content):
            chunk = content[i:i + CHUNK]
            yield f"data: {json.dumps({'token': chunk})}\n\n"
            i += CHUNK
            await asyncio.sleep(0.012)

        # Maxx mode: emit a stream marker, then run watchdog and emit result
        watchdog = None
        if body.maxx_mode and content.strip():
            yield f"data: {json.dumps({'watchdog_pending': True})}\n\n"
            watchdog = await call_emergent_watchdog(content)
            yield f"data: {json.dumps({'watchdog': watchdog})}\n\n"
            provider = (provider or "deepseek") + "+emergent-watchdog"

        await _persist_turn(user_id, body.session_id or "",
                            body.prompt, content, provider, watchdog=watchdog,
                            project_id=body.project_id,
                            shipped_task_id=handoff_task_id)

        # Iter 145 — ORA shadow-learning. For ALL users, detect
        # low-confidence AUREM replies and fire a background ORA call
        # whose output is logged (never shown) so ORA can learn
        # patterns from real weak-spots. NEVER replaces user reply.
        try:
            from services.ora_learning import maybe_log_ora_escalation
            asyncio.create_task(maybe_log_ora_escalation(
                db=get_db(),
                user_id=user_id,
                session_id=body.session_id or "",
                project_id=body.project_id,
                prompt=body.prompt or "",
                aurem_response=content or "",
                provider=provider,
            ))
        except Exception:
            pass

        # ORA council log (Mode A/B only) + project brain update.
        # Fire-and-forget; never blocks user reply.
        # BUG 5 fix — Mode D (debug) and E (audit) replies were getting
        # logged as A or B which poisons the training data. Only
        # conversational modes (A/B) belong in ora_council_logs from this
        # path; Mode C uses log_code_task, Mode D/E aren't part of the
        # fine-tuning corpus.
        _classified_mode = result.get("mode") if isinstance(result, dict) else None
        if _classified_mode in (None, "A", "B"):
            try:
                from services.ora_council_logger import log_conversational
                from services.project_brain import update_brain_from_conversation
                council_mode = "B" if "aurem-handoff" in (content or "") else "A"
                _db = get_db()
                if _db is not None:
                    await log_conversational(
                        db=_db,
                        mode=council_mode,
                        user_message=body.prompt or "",
                        ora_reply=content or "",
                        user_id=user_id,
                        project_id=body.project_id,
                    )
                    # Lightweight conversation → brain update (rejections, decisions, stack)
                    if body.project_id and body.project_id != "home":
                        asyncio.create_task(update_brain_from_conversation(
                            db=_db,
                            project_id=body.project_id,
                            user_message=body.prompt or "",
                            ora_reply=content or "",
                            mode=council_mode,
                        ))
            except Exception:
                pass

        if body.session_id:
            asyncio.create_task(
                _maybe_set_title(user_id, body.session_id, body.prompt)
            )
        tokens_remaining = await _deduct_tokens(user_id, content)

        done_payload = {
            "done": True,
            "provider": provider,
            "session_id": body.session_id,
            "tokens_remaining": tokens_remaining,
            "council": bool(result.get("council")),
            # Iter 85 — paths the model actually read this turn.
            # Frontend uses this to enforce ABSOLUTE NEGATIVE rule (d):
            # any path inside the ```aurem-handoff fence that is NOT in
            # this set is a fabricated citation, so the Ship button is
            # suppressed.
            "verified_paths": result.get("verified_paths") or [],
            # Iter 119 — web sources (URLs the model actually fetched via
            # Tavily / Firecrawl / fetch_url). Frontend renders these as
            # 🌐 citation chips below the assistant message so users can
            # one-click verify external claims.
            "web_sources": result.get("web_sources") or [],
        }
        yield f"data: {json.dumps(done_payload)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/history")
async def chat_history(
    session_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Return last 20 turns of a session for the current user."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None or not session_id:
        return {"ok": True, "messages": [], "session_id": session_id}
    doc = await db.chat_sessions.find_one(
        {"session_id": session_id, "user_id": user["user_id"]},
        {"_id": 0, "turns": 1, "title": 1},
    )
    turns = ((doc or {}).get("turns") or [])[-20:]
    return {
        "ok": True,
        "messages": turns,
        "session_id": session_id,
        "title": (doc or {}).get("title", ""),
    }


@router.get("/sessions")
async def chat_sessions_list(
    project_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Return up to 20 most-recent chat sessions for the current user.
    Filter to a specific project_id when provided; pass 'home' to get
    sessions that aren't bound to any project."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        return {"ok": True, "sessions": []}
    q = {"user_id": user["user_id"]}
    if project_id == "home":
        # Home tab shows un-pinned sessions PLUS legacy sessions that have
        # no project_id field at all (created before per-project chats).
        q["$or"] = [{"project_id": None}, {"project_id": {"$exists": False}}]
    elif project_id:
        q["project_id"] = project_id
    cursor = db.chat_sessions.find(
        q,
        {
            "_id": 0, "session_id": 1, "title": 1, "project_id": 1,
            "last_message": 1, "updated_at": 1, "created_at": 1,
        },
    ).sort("updated_at", -1).limit(20)
    sessions = await cursor.to_list(length=20)
    return {"ok": True, "sessions": sessions}


class TurnShippedBody(BaseModel):
    session_id: str
    turn_index: int
    task_id: str


@router.post("/turn/shipped")
async def chat_turn_shipped(
    body: TurnShippedBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Record that an assistant turn was shipped via CTO so the Ship button
    doesn't re-appear on refresh/rejoin. Stores `task_id` on the turn doc.

    Iter 34 — defensive validation: refuse to write past the end of the
    turns array. MongoDB silently creates sparse `turns[N]` entries when
    asked to $set on an out-of-range index, which corrupts the document
    and brings the Ship button back on every refresh. Front-end already
    sends a DB-correct index, but legacy clients / stale tabs might not.
    """
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    if body.turn_index < 0:
        raise HTTPException(400, "turn_index must be >= 0")

    # Look up the live turn count before we write
    sess = await db.chat_sessions.find_one(
        {"session_id": body.session_id, "user_id": user["user_id"]},
        {"_id": 0, "turns": 1},
    )
    if not sess:
        raise HTTPException(404, "Session not found")
    turns = sess.get("turns") or []
    if body.turn_index >= len(turns):
        # Off-by-one or stale index — don't corrupt the doc. Fall back to
        # marking the latest assistant turn as shipped (safest default).
        last_asst = max(
            (i for i, t in enumerate(turns) if (t or {}).get("role") == "assistant"),
            default=None,
        )
        if last_asst is None:
            raise HTTPException(409,
                                "Cannot record shipped state — no assistant "
                                "turns in this session yet")
        body = TurnShippedBody(session_id=body.session_id,
                               turn_index=last_asst,
                               task_id=body.task_id)

    set_field = f"turns.{body.turn_index}.shipped_task_id"
    await db.chat_sessions.update_one(
        {"session_id": body.session_id, "user_id": user["user_id"]},
        {"$set": {set_field: body.task_id}},
    )
    return {"ok": True, "turn_index": body.turn_index}


class FeedbackBody(BaseModel):
    session_id: str
    turn_index: int       # index within the turns array (assistant turn)
    vote: str             # 'up' | 'down'
    comment: Optional[str] = None


@router.post("/feedback")
async def chat_feedback(
    body: FeedbackBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Record like/dislike on an assistant turn. Used for future fine-tuning
    + lets the UI show that feedback was captured."""
    user = await current_dev(authorization)
    if body.vote not in ("up", "down"):
        raise HTTPException(400, "vote must be 'up' or 'down'")
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    set_field = f"turns.{body.turn_index}.feedback"
    await db.chat_sessions.update_one(
        {"session_id": body.session_id, "user_id": user["user_id"]},
        {"$set": {set_field: {
            "vote": body.vote,
            "comment": body.comment,
            "ts": time.time(),
        }}},
    )
    return {"ok": True}


@router.delete("/sessions/{session_id}")
async def chat_session_delete(
    session_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Delete a single chat session belonging to the current user."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    r = await db.chat_sessions.delete_one(
        {"session_id": session_id, "user_id": user["user_id"]}
    )
    return {"ok": True, "deleted": r.deleted_count}


@router.delete("/sessions/{session_id}/messages")
async def chat_session_clear_messages(
    session_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 131 — wipe all turns in a session but KEEP the session
    alive (preserves session_id + title + project link). Powers the
    'Clear chat' button in the chat-window toolbar so a user can
    reset a long conversation without losing its sidebar entry."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    r = await db.chat_sessions.update_one(
        {"session_id": session_id, "user_id": user["user_id"]},
        {"$set": {
            "turns": [],
            "updated_at": time.time(),
            "last_message": "",
        }},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "session not found")
    return {"ok": True, "cleared": True, "session_id": session_id}


# ─── Iter 53 — Post-commit wrap-up message ─────────────────────────────
# When a Mode C task finishes (status=done) the chat used to fall silent.
# The user only saw "✅ Pushed <sha>" on the status card and had to ask
# "is it fixed?" — which then timed out because we re-classified that as
# a new task with no codebase context. This endpoint produces the
# explicit closing message ORA owes the user: what was changed, whether
# the original ask is likely resolved, and one concrete verification
# step. Idempotent — only fires once per task.

class TaskFollowupBody(BaseModel):
    session_id: str
    task_id: str


@router.post("/task-followup")
async def chat_task_followup(
    body: TaskFollowupBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Generate a closing assistant message for a completed Mode C task,
    persist it to the chat session, and return it so the frontend can
    append it inline. Idempotent — second call returns the cached text."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    task = await db.cto_tasks.find_one(
        {"task_id": body.task_id, "user_id": user["user_id"]}, {"_id": 0}
    )
    if not task:
        raise HTTPException(404, "Task not found")
    if task.get("status") not in ("done", "failed"):
        raise HTTPException(
            409,
            f"Task not yet complete (status={task.get('status')})",
        )

    # Idempotency — return cached if we generated one already.
    cached = task.get("followup_message")
    if cached:
        return {"ok": True, "message": cached, "cached": True}

    files = task.get("files_changed") or task.get("files") or []
    summary = (task.get("result") or "").strip()
    original = (task.get("task") or "").strip()
    sha = task.get("commit_sha")
    err = (task.get("error") or "").strip()

    if task.get("status") == "failed":
        message = _build_failed_followup(original, err, files)
    else:
        try:
            message = await _generate_done_followup(
                original=original, summary=summary, files=files, sha=sha,
            )
        except Exception:
            logger.exception("task-followup LLM generation failed; "
                             "falling back to deterministic template")
            message = _build_done_fallback(original, summary, files, sha)

    # Persist on the task doc for idempotency.
    await db.cto_tasks.update_one(
        {"task_id": body.task_id},
        {"$set": {"followup_message": message,
                  "followup_at": time.time()}},
    )

    # Append to the chat session so a refresh keeps it visible.
    sess = await db.chat_sessions.find_one(
        {"session_id": body.session_id, "user_id": user["user_id"]},
        {"_id": 0, "turns": 1},
    )
    if sess is not None:
        new_turn = {
            "role": "assistant",
            "content": message,
            "ts": time.time(),
            "provider": "ora",
            "kind": "task_followup",
            "task_id": body.task_id,
        }
        await db.chat_sessions.update_one(
            {"session_id": body.session_id, "user_id": user["user_id"]},
            {"$push": {"turns": {"$each": [new_turn], "$slice": -40}},
             "$set": {"updated_at": time.time(),
                      "preview": message[:120]}},
        )

    return {"ok": True, "message": message, "cached": False}


def _build_failed_followup(original: str, err: str, files: list[str]) -> str:
    """Deterministic — no LLM. Fail-fast, fail-honest."""
    bits = ["❌ Task failed — nothing was committed.\n"]
    if err:
        snippet = err[:400] + ("…" if len(err) > 400 else "")
        bits.append(f"**Error:** `{snippet}`\n")
    if files:
        bits.append("**Files I tried to touch:** "
                    + ", ".join(f"`{f}`" for f in files[:6]) + "\n")
    bits.append(
        "Want me to retry with a smaller scope? Or paste the exact "
        "error / steps to reproduce and I'll diagnose it in Mode D first."
    )
    return "".join(bits)


def _build_done_fallback(original: str, summary: str,
                         files: list[str], sha: Optional[str]) -> str:
    """Used when the follow-up LLM call itself fails — never block the UX."""
    file_list = ", ".join(f"`{f}`" for f in files[:8]) or "_no files reported_"
    return (
        f"✅ **Done — `{sha or 'commit'}` pushed.**\n\n"
        f"**Changed:** {file_list}\n\n"
        f"**Summary:** {summary or 'See diff for details.'}\n\n"
        "**Verify it:** pull the latest, restart, and re-trigger the "
        "original flow. Reply here if anything's still off — I'll "
        "diagnose without burning another quota."
    )


_FOLLOWUP_SYS = (
    "You are ORA, an AI engineering lead. A code task just completed. "
    "Write a SHORT closing message (max 6 short lines) to the user with "
    "EXACTLY this structure:\n\n"
    "Line 1: ✅ one-line summary of what was actually changed.\n"
    "Line 2: **Files:** `path1`, `path2` (max 5, real names only).\n"
    "Line 3: **Likely resolves original ask?** Yes / Partially / No "
    "— with a one-clause reason. Be honest. If the commit feels off-"
    "scope or generic vs. the user's ask, say 'Partially' or 'No'.\n"
    "Line 4: **Verify it:** one concrete step the user can take in "
    "<30 seconds to confirm (a curl, a button to click, a page to open, "
    "etc.). Be specific.\n"
    "Line 5 (optional): **Next:** one specific follow-up if needed.\n\n"
    "Rules: no fluff, no 'great question', no emoji except the leading "
    "✅. Plain English. No markdown headers. No code fences. Keep total "
    "under 90 words."
)


async def _generate_done_followup(original: str, summary: str,
                                  files: list[str],
                                  sha: Optional[str]) -> str:
    """Single ~320-token DeepSeek call. Strict format, low temperature.
    The system prompt does the heavy lifting — keep the user message
    tight so the model can't wander."""
    file_list = ", ".join(files[:8]) if files else "(none reported)"
    user_msg = (
        f"ORIGINAL USER ASK:\n{original or '(missing)'}\n\n"
        f"COMMIT SHA: {sha or '(none)'}\n"
        f"FILES CHANGED: {file_list}\n"
        f"COMMIT SUMMARY: {summary or '(none)'}\n\n"
        "Write the closing message now, following the structure exactly."
    )
    res = await call_llm_with_meta(
        system=_FOLLOWUP_SYS,
        user=user_msg,
        max_tokens=320,
        mode="chat",
    )
    text = (res.get("content") or "").strip()
    if not text:
        return _build_done_fallback(original, summary, files, sha)
    return text
