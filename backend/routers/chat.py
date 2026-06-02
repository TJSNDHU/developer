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
    max_tool_iters: int = Field(8, ge=0, le=12)
    maxx_mode: bool = False
    project_id: Optional[str] = Field(None, max_length=128)
    # Iter 38: agent selector. "auto" routes via existing model-routing
    # logic in orchestrator.py (DeepSeek/Claude). "ora" calls the founder's
    # own aurem.live ORA endpoint. Other values currently fall through to
    # "auto" so adding new agents later is backwards-compatible.
    agent: Optional[str] = Field("auto", max_length=32)
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

    Returns True only when the payload contains something a debugger
    can actually use:
      * A console error with a non-trivial message (>5 chars)
      * A network error with HTTP status in 400-599 AND a real URL
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
        if isinstance(st, int) and 400 <= st < 600 and ne.get("url"):
            return True
    if payload.get("stack_traces"):
        return True
    return False


def classify_intent(message: str, f12_payload: Optional[dict]) -> str:
    """Returns one of: 'A','B','C','D','E'. Order matters."""
    from services.mode_d_debugger import is_debug_request
    from services.mode_e_auditor  import is_audit_request

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
                        project_id: Optional[str] = None) -> None:
    """Append user+assistant turns to db.chat_sessions, capped at 40 turns.
    Tags the session with the project it belongs to (None == Home/global)."""
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
    result = await chat_with_tools(
        prompt=body.prompt,
        jwt_token=jwt_token,
        system=(extra_sys + "\n\n" if extra_sys else None),
        max_iters=min(body.max_tool_iters, 6),
        session_id=body.session_id,
        mongo_client=None,
        user_id=user["user_id"],
        project_id=body.project_id,
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



@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatBody,
    authorization: Optional[str] = Header(None),
):
    """SSE token-streaming chat. Iter 45: rate-limited to 30 req/min per IP."""
    from services.rate_limiter import check_rate_limit, client_ip_from_request
    if not check_rate_limit(f"chat:{client_ip_from_request(request)}", 30):
        raise HTTPException(429, "Rate limit exceeded: 30 chats/min/IP")
    user = await current_dev(authorization)
    jwt_token = authorization.split(" ", 1)[1] if authorization else ""
    user_id = user.get("user_id", "")

    # Iter 38: ORA is founder-only. The ORA API key is shared across all
    # founders, so we gate at the surface to avoid customer quota burn.
    if (body.agent or "").lower() == "ora":
        from services.usage import is_founder_email
        if not is_founder_email(user.get("email")):
            raise HTTPException(403, "ORA agent is founder-only")

    repo_ctx = await get_repo_context(user_id, body.project_id or "")
    url_ctx = await build_url_context(body.prompt)
    extra_sys = "\n\n".join(s for s in (repo_ctx, url_ctx) if s)

    async def gen():
        import time as _t
        t_start = _t.monotonic()
        # Iter 36: hard wall-clock ceiling — if the worker doesn't return
        # within HARD_TIMEOUT_S we abort and emit a friendly error so the
        # UI can never "thinking…" for 15 minutes again.
        HARD_TIMEOUT_S = 90.0
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
                    await q.put({
                        "type": "tick",
                        "elapsed_s": elapsed,
                        "activity": activity["label"],
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

                # ─── Iter 42 — Mode classifier + Mode D/E early routing ───
                # Decide A/B/C/D/E once and broadcast to frontend so the UI
                # can show the live pill before tokens stream.
                _mode = classify_intent(body.prompt or "", body.f12_payload)
                await q.put({"type": "mode", "mode": _mode})

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
                        logger.info(
                            "ora upstream unavailable (%s) — falling back to AUREM",
                            getattr(ora_err, "status_code", "?"),
                        )
                        activity["label"] = "ORA unavailable — switching to AUREM CTO…"
                        # Fall through to the AUREM/orchestrator path below.

                activity["label"] = "thinking…"
                result = await chat_with_tools(
                    prompt=body.prompt,
                    jwt_token=jwt_token,
                    system=(extra_sys + "\n\n" if extra_sys else None),
                    max_iters=min(max(body.max_tool_iters, 8), 12),
                    session_id=body.session_id,
                    mongo_client=None,
                    user_id=user_id,
                    project_id=body.project_id,
                    activity_hook=lambda s: activity.__setitem__("label", s),
                )
                await q.put({"type": "result", "result": result})
            except Exception as e:
                logger.exception("chat_stream orchestrator failed")
                await q.put({"type": "error", "error": str(e)})
            finally:
                stop_event.set()

        ticker_t = asyncio.create_task(_ticker())
        worker_t = asyncio.create_task(_worker())

        result = None
        deadline_at = _t.monotonic() + HARD_TIMEOUT_S
        while True:
            try:
                ev = await asyncio.wait_for(
                    q.get(), timeout=max(0.1, deadline_at - _t.monotonic()),
                )
            except asyncio.TimeoutError:
                # Wall-clock blown. Cancel everything, tell the user.
                worker_t.cancel()
                ticker_t.cancel()
                yield (
                    "data: " + json.dumps({
                        "error": (
                            f"AUREM timed out after {int(HARD_TIMEOUT_S)}s. "
                            "Reload and try a smaller question, or ask me "
                            "to narrow scope (e.g. 'just check file X')."
                        ),
                    }) + "\n\n"
                )
                return
            if ev["type"] == "tick":
                yield (
                    "data: " + json.dumps({
                        "thinking":  True,
                        "elapsed_s": ev["elapsed_s"],
                        "activity":  ev["activity"],
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
                            project_id=body.project_id)

        # iter 41 — ORA council log (Mode A/B) + project brain update.
        # Fire-and-forget; never blocks user reply.
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
