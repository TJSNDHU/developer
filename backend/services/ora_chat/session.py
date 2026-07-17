"""
services/ora_chat/session.py — Iter 212m-238

Mongo-backed conversation state with a rolling-summary sliding window.

Design:
  - One document per session in `ora_chat_sessions`.
  - Full transcript kept in `messages` (unbounded — for audit/replay).
  - LLM calls see ONLY the last 6 turns verbatim + a rolling summary
    of everything before that.
  - Summary is COMPUTED ONCE per "generation" (when turn 7 arrives),
    then updated INCREMENTALLY as new turns roll past the window —
    each summarization is a single GLM-5.2 call (cheapest model),
    never a re-summarization of the whole history.

Cost impact:
  - Naive full-history: cost grows quadratic-ish with turn count.
  - This approach: cost is bounded per turn (6 turns + 1 short summary).
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from cto_services.db import get_db


# ── Config ──────────────────────────────────────────────────────────
WINDOW_TURNS = 6                 # verbatim turns sent to the LLM
SUMMARY_MAX_CHARS = 2000         # cap on rolling summary size
SUMMARY_MODEL = "z-ai/glm-5.2"   # cheapest capable model for summarization


# ── Session CRUD ────────────────────────────────────────────────────
async def create_session(user_id: str, title: str = "") -> dict:
    """Create a new session. Returns the freshly inserted doc."""
    db = get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    now = time.time()
    doc = {
        "session_id":       uuid.uuid4().hex,
        "user_id":          user_id,
        "title":            (title or "New chat").strip()[:80],
        "created_at":       now,
        "updated_at":       now,
        "messages":         [],   # {role, content, ts, route, model,
                                  #  input_tokens, output_tokens,
                                  #  temperature, cost_usd}
        "rolling_summary":  "",
        "summary_up_to":    0,    # index into `messages`
        "message_count":    0,
    }
    await db.ora_chat_sessions.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def get_session(session_id: str, user_id: str) -> Optional[dict]:
    """Fetch a session — enforces ownership by `user_id`."""
    db = get_db()
    if db is None:
        return None
    doc = await db.ora_chat_sessions.find_one(
        {"session_id": session_id, "user_id": user_id},
    )
    if not doc:
        return None
    doc.pop("_id", None)
    return doc


async def list_sessions(user_id: str, limit: int = 30) -> list[dict]:
    """List a user's sessions, most-recent first, without full message
    bodies (for the sidebar picker)."""
    db = get_db()
    if db is None:
        return []
    cursor = db.ora_chat_sessions.find(
        {"user_id": user_id},
        {"_id": 0, "session_id": 1, "title": 1, "created_at": 1,
         "updated_at": 1, "message_count": 1},
    ).sort("updated_at", -1).limit(limit)
    return [row async for row in cursor]


async def append_message(session_id: str, user_id: str, *,
                         role: str, content: str,
                         route: str = "", model: str = "",
                         temperature: Optional[float] = None,
                         input_tokens: int = 0, output_tokens: int = 0,
                         cost_usd: float = 0.0) -> bool:
    """Append a message + bump counters. Returns True on success."""
    db = get_db()
    if db is None:
        return False
    entry: dict = {
        "role":    role,
        "content": content,
        "ts":      time.time(),
    }
    if route:              entry["route"]             = route
    if model:              entry["model"]             = model
    if temperature is not None: entry["temperature"]  = temperature
    if input_tokens:       entry["input_tokens"]      = input_tokens
    if output_tokens:      entry["output_tokens"]     = output_tokens
    if cost_usd:           entry["cost_usd"]          = cost_usd
    r = await db.ora_chat_sessions.update_one(
        {"session_id": session_id, "user_id": user_id},
        {"$push": {"messages": entry},
         "$inc":  {"message_count": 1},
         "$set":  {"updated_at": time.time()}},
    )
    return r.matched_count > 0


# ── Sliding-window prompt builder ───────────────────────────────────
def _messages_to_llm_format(msgs: list[dict]) -> list[dict]:
    """Reduce stored message docs to OpenAI-format role/content pairs."""
    out: list[dict] = []
    for m in msgs:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        out.append({"role": role, "content": m.get("content", "")})
    return out


async def build_llm_messages(session: dict) -> list[dict]:
    """Return the messages array to send to the LLM.

    Layout:
      [
        (optional) {"role": "system", "content": "Prior conversation summary: ..."},
        <last 6 turns verbatim in chronological order>
      ]

    A system prompt is added SEPARATELY by the caller (safety.SYSTEM_PROMPT).
    """
    msgs: list[dict] = session.get("messages") or []
    if len(msgs) <= WINDOW_TURNS:
        return _messages_to_llm_format(msgs)

    tail = msgs[-WINDOW_TURNS:]
    summary = (session.get("rolling_summary") or "").strip()
    out: list[dict] = []
    if summary:
        out.append({
            "role": "system",
            "content": f"Prior conversation summary (older turns collapsed):\n{summary}",
        })
    out.extend(_messages_to_llm_format(tail))
    return out


async def maybe_update_summary(session_id: str, user_id: str) -> None:
    """Trigger a summary refresh iff the transcript has grown past the
    window AND unsummarized turns exist. Idempotent + cheap on no-op.
    """
    db = get_db()
    if db is None:
        return
    doc = await db.ora_chat_sessions.find_one(
        {"session_id": session_id, "user_id": user_id},
        {"messages": 1, "rolling_summary": 1, "summary_up_to": 1},
    )
    if not doc:
        return
    msgs = doc.get("messages") or []
    total = len(msgs)
    if total <= WINDOW_TURNS:
        return  # everything still fits in the window
    up_to = int(doc.get("summary_up_to", 0) or 0)
    frontier = total - WINDOW_TURNS       # index up to which we should summarize
    if frontier <= up_to:
        return  # nothing new to fold in

    to_fold = msgs[up_to:frontier]
    if not to_fold:
        return

    prior_summary = (doc.get("rolling_summary") or "").strip()
    fold_text_parts: list[str] = []
    for m in to_fold:
        role = m.get("role", "")
        content = (m.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        fold_text_parts.append(f"{role.upper()}: {content[:600]}")
    fold_text = "\n".join(fold_text_parts)[:4000]

    prompt = (
        "You are summarizing older turns of a conversation so a chat "
        "assistant can maintain context without re-reading them. Update "
        "the running summary below with the new turns.\n\n"
        f"CURRENT SUMMARY:\n{prior_summary or '(empty)'}\n\n"
        f"NEW TURNS TO FOLD IN:\n{fold_text}\n\n"
        "Return ONLY the new combined summary. Keep it under "
        f"{SUMMARY_MAX_CHARS} characters. Preserve concrete details "
        "(names, numbers, decisions). Drop small talk. No preamble."
    )

    # Best-effort — a summarization failure should never break the
    # chat itself; we simply skip the update and try again next turn.
    from services.ora_chat.providers import one_shot
    from services.ora_chat.router import resolve
    cfg = resolve("fallback")   # GLM-5.2 route
    new_summary, _usage, err = await one_shot(
        model=SUMMARY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        top_p=cfg["top_p"],
        presence_penalty=cfg["presence_penalty"],
        max_tokens=800,
    )
    if err or not new_summary:
        return

    await db.ora_chat_sessions.update_one(
        {"session_id": session_id, "user_id": user_id},
        {"$set": {
            "rolling_summary": new_summary.strip()[:SUMMARY_MAX_CHARS],
            "summary_up_to":   frontier,
            "updated_at":      time.time(),
        }},
    )
