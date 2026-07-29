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
# Iter 212m-239 (single-user revision) — long-context sessions.
# Instead of a fixed 6-turn cutoff, keep the FULL transcript in the
# LLM window until the estimated token count approaches the model's
# context ceiling. DeepSeek V3 has ~128K; we leave ~28K headroom for
# response + system prompt + house rules.
CONTEXT_TOKEN_CEILING = int(__import__("os").getenv("ORA_CONTEXT_TOKEN_CEILING", "100000"))
# When we cross the ceiling, we summarize the OLDEST chunk of the
# transcript down and keep the newest turns verbatim. `TAIL_TOKEN_BUDGET`
# is the target verbatim-tail size (leaving room for the summary + new
# turn + system prompt).
TAIL_TOKEN_BUDGET = int(__import__("os").getenv("ORA_TAIL_TOKEN_BUDGET", "70000"))

# Rough char→token estimator — deliberately conservative (higher
# multiplier = more tokens per char = triggers summarization sooner).
# OpenAI-family averages ~3.8 chars/token for English + code; we round
# down to be safe for Hinglish + code + JSON.
CHARS_PER_TOKEN = 3.5

SUMMARY_MAX_CHARS = 4000         # cap on rolling summary size
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
                         cost_usd: float = 0.0,
                         message_id: Optional[str] = None,
                         ungrounded: Optional[list] = None,
                         prompt_sha256: str = "",
                         component_sizes: Optional[dict] = None,
                         review: Optional[dict] = None) -> bool:
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
    # Iter 264 Fix A/C — grounding + prompt-audit metadata.
    if message_id:         entry["message_id"]        = message_id
    if ungrounded:         entry["ungrounded"]        = ungrounded[:20]
    if prompt_sha256:      entry["prompt_sha256"]     = prompt_sha256
    if component_sizes:    entry["component_sizes"]   = component_sizes
    # Iter 268 — adversarial review outcome (flags/regen/caveats).
    if review:             entry["review"]            = review
    r = await db.ora_chat_sessions.update_one(
        {"session_id": session_id, "user_id": user_id},
        {"$push": {"messages": entry},
         "$inc":  {"message_count": 1},
         "$set":  {"updated_at": time.time()}},
    )
    return r.matched_count > 0


def _estimate_tokens(text: str) -> int:
    """Conservative char→token estimate. Used to decide when to fold
    older turns into the summary. Never exact — a small over-count is
    fine (triggers summarize a little early); under-count is
    dangerous (overflows the context window)."""
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN) + 1


def _messages_token_estimate(msgs: list[dict]) -> int:
    return sum(_estimate_tokens(m.get("content", "") or "")
               for m in msgs if isinstance(m, dict))


# ── Sliding-window prompt builder ───────────────────────────────────
def _messages_to_llm_format(msgs: list[dict]) -> list[dict]:
    """Reduce stored message docs to OpenAI-format role/content pairs."""
    out: list[dict] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        out.append({"role": role, "content": m.get("content", "")})
    return out


async def build_llm_messages(session: dict) -> list[dict]:
    """Return the messages array to send to the LLM.

    Personal single-user contract (Iter 212m-239):
      - Full transcript preserved verbatim while estimated tokens stay
        under CONTEXT_TOKEN_CEILING.
      - Above the ceiling, older turns are collapsed into
        `rolling_summary` (updated by `maybe_update_summary`) and only
        the tail (≥ TAIL_TOKEN_BUDGET headroom) is sent verbatim.

    A caller-provided system prompt is added SEPARATELY by the router
    (safety.assemble_system_prompt).
    """
    msgs: list[dict] = session.get("messages") or []
    llm_form = _messages_to_llm_format(msgs)
    if _messages_token_estimate(llm_form) <= CONTEXT_TOKEN_CEILING:
        return llm_form  # full history fits — send everything

    # Over ceiling → prepend summary + walk tail from the newest
    # backwards until we've used TAIL_TOKEN_BUDGET.
    summary = (session.get("rolling_summary") or "").strip()
    tail: list[dict] = []
    tokens = 0
    for m in reversed(llm_form):
        t = _estimate_tokens(m["content"])
        if tokens + t > TAIL_TOKEN_BUDGET and tail:
            break
        tail.insert(0, m)
        tokens += t
    out: list[dict] = []
    if summary:
        out.append({
            "role": "system",
            "content": f"Prior conversation summary (older turns collapsed):\n{summary}",
        })
    out.extend(tail)
    return out


async def maybe_update_summary(session_id: str, user_id: str) -> None:
    """Trigger a summary refresh iff the transcript is over the
    CONTEXT_TOKEN_CEILING AND has unsummarized older turns.

    Idempotent + cheap on no-op: a single Mongo find, and only calls
    GLM-5.2 when there's genuinely new content to fold in.
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
    llm_form = _messages_to_llm_format(msgs)
    total_tokens = _messages_token_estimate(llm_form)
    if total_tokens <= CONTEXT_TOKEN_CEILING:
        return  # still fits, no summarization needed

    # Find the "frontier" — everything OLDER than the TAIL_TOKEN_BUDGET
    # window gets folded into the summary. Walk from the newest
    # backwards until we've reserved TAIL_TOKEN_BUDGET of tail.
    tail_tokens = 0
    frontier = len(llm_form)
    for i in range(len(llm_form) - 1, -1, -1):
        tail_tokens += _estimate_tokens(llm_form[i]["content"])
        if tail_tokens >= TAIL_TOKEN_BUDGET:
            frontier = i
            break

    up_to = int(doc.get("summary_up_to", 0) or 0)
    if frontier <= up_to:
        return  # nothing new to fold in

    to_fold = llm_form[up_to:frontier]
    if not to_fold:
        return

    prior_summary = (doc.get("rolling_summary") or "").strip()
    parts: list[str] = []
    for m in to_fold:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = (m.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        parts.append(f"{role.upper()}: {content[:1200]}")
    fold_text = "\n".join(parts)[:12000]

    prompt = (
        "You are summarizing older turns of a conversation so the chat "
        "assistant can maintain context without re-reading them. Update "
        "the running summary below with the new turns.\n\n"
        f"CURRENT SUMMARY:\n{prior_summary or '(empty)'}\n\n"
        f"NEW TURNS TO FOLD IN:\n{fold_text}\n\n"
        "Return ONLY the new combined summary. Keep it under "
        f"{SUMMARY_MAX_CHARS} characters. Preserve concrete details "
        "(names, numbers, decisions, code snippets). Drop small talk. "
        "No preamble."
    )

    from services.ora_chat.providers import one_shot
    from services.ora_chat.router import resolve
    cfg = resolve("fallback")
    new_summary, _usage, err = await one_shot(
        model=SUMMARY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        top_p=cfg["top_p"],
        presence_penalty=cfg["presence_penalty"],
        max_tokens=1500,
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
