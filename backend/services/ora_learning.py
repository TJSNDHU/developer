"""
ora_learning.py — Iter 145 silent ORA shadow-logging pipeline.

Goal: collect real-world weak-point examples where AUREM's response was
low-confidence, so ORA can learn from them offline. We do NOT replace
the user-facing reply (would double costs and confuse UX); we just
shadow-log AUREM_answer + ORA_answer + the trigger reason into a new
`ora_learning_logs` collection.

Triggered fire-and-forget from routers/chat.py right after AUREM's
turn is persisted. Never raises — if anything fails we drop the sample.
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

from .ora_client import call_ora, is_ora_available


# Phrases AUREM emits when it lacks confidence. Lowercased for substr match.
_LOW_CONFIDENCE_PATTERNS: tuple[str, ...] = (
    "i'm not sure",
    "i am not sure",
    "i don't know",
    "i do not know",
    "i'm not certain",
    "not enough context",
    "without more context",
    "could you clarify",
    "can you clarify",
    "i cannot help",
    "i can't help",
    "i'm unable to",
    "as an ai language model",
    "i'm sorry, but",
    "[error]",
    "task failed",
    "vanguard verify agent blocked",
)


def _detect_low_confidence(prompt: str, response: str) -> Optional[str]:
    """Return a short reason string if response looks low-confidence, else None."""
    if not response:
        return "empty_response"
    rlow = response.lower()
    for pat in _LOW_CONFIDENCE_PATTERNS:
        if pat in rlow:
            return f"phrase:{pat}"
    # Heuristic: long prompt (substantive ask) + tiny answer = likely punted.
    if len(prompt) > 200 and len(response.strip()) < 80:
        return "short_answer_on_long_prompt"
    # Heuristic: response is mostly a clarifying question back at the user.
    qmarks = response.count("?")
    if qmarks >= 2 and len(response) < 300:
        return "clarifying_question_storm"
    return None


async def maybe_log_ora_escalation(
    *,
    db,
    user_id: str,
    session_id: str,
    project_id: Optional[str],
    prompt: str,
    aurem_response: str,
    provider: Optional[str],
) -> None:
    """Fire-and-forget. Detect low-confidence → call ORA in background →
    persist both responses to `ora_learning_logs`. Never raises."""
    try:
        if db is None:
            return
        if os.environ.get("ORA_LEARNING_DISABLED") == "1":
            return
        if not is_ora_available():
            return
        reason = _detect_low_confidence(prompt or "", aurem_response or "")
        if not reason:
            return
        # Rate-limit: at most N per user per hour to cap blast radius.
        try:
            cutoff = time.time() - 3600
            recent = await db.ora_learning_logs.count_documents(
                {"user_id": user_id, "ts": {"$gte": cutoff}},
            )
            cap = int(os.environ.get("ORA_LEARNING_HOURLY_CAP", "20"))
            if recent >= cap:
                return
        except Exception:
            pass

        # Call ORA with the same prompt. system_hint guides ORA to act
        # as a senior reviewer evaluating AUREM's reply, not just re-answer.
        try:
            res = await call_ora(
                message=(prompt or "")[:4000],
                session_id=f"learn-{session_id}"[:128] if session_id else None,
                system_hint=(
                    "You are ORA reviewing AUREM's reply. Provide a "
                    "complete, confident answer to the user's question."
                ),
                scope="ora",
                timeout=45.0,
            )
            ora_text = (res.get("reply") or res.get("message")
                        or res.get("content") or "")[:8000]
        except Exception as e:
            ora_text = ""
            ora_err = f"{type(e).__name__}: {str(e)[:200]}"
        else:
            ora_err = None

        await db.ora_learning_logs.insert_one({
            "ts": time.time(),
            "user_id": user_id,
            "session_id": session_id,
            "project_id": project_id,
            "provider": provider,
            "reason": reason,
            "prompt": (prompt or "")[:4000],
            "aurem_response": (aurem_response or "")[:8000],
            "ora_response": ora_text,
            "ora_error": ora_err,
            "version": 1,
        })
    except Exception:
        # Strict invariant: shadow-logging never crashes the request path.
        return


__all__ = ["maybe_log_ora_escalation"]
