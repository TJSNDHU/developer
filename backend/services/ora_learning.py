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

import logging
import os
import re
import time
from collections import Counter
from typing import Optional

from .ora_client import call_ora, is_ora_available

logger = logging.getLogger(__name__)


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


# ──────────────────────────────────────────────────────────────────
# Iter 212m — Session Learning System
#
# After every chat session turn we mine the session's prompts for:
#   1. file paths the user actually touched (HOT files)
#   2. stack signals (framework / language / tooling keywords)
# and persist them in `ora_patterns` so subsequent turns can be
# pre-loaded with what this user/project tends to care about.
#
# Helper functions are exported so the orchestrator can warm-inject
# the learned context into the system prompt (`load_user_patterns`),
# and chat.py can fire-and-forget the extractor at session end
# (`extract_session_patterns`).
# ──────────────────────────────────────────────────────────────────

# Regex matches `foo/bar/baz.ext` or `baz.ext` — tight enough to skip
# URLs, dotted python identifiers, and prose mentions of file types.
_FILE_PATH_RX = re.compile(
    r"(?<![A-Za-z0-9/])"               # boundary before
    r"([A-Za-z0-9_.\-/]+\."            # path body + dot
    r"(?:py|js|jsx|ts|tsx|md|json|yml|yaml|toml|css|html|sh|sql))"
    r"(?![A-Za-z0-9])"                 # boundary after
)

# Stack-signal keywords (case-insensitive substring match).
_STACK_SIGNALS: tuple[str, ...] = (
    "fastapi", "flask", "django", "express", "next.js", "nextjs",
    "react", "vue", "svelte", "angular", "tailwind",
    "mongo", "mongodb", "postgres", "postgresql", "mysql", "redis",
    "sqlite", "supabase", "firebase",
    "celery", "rabbitmq", "kafka", "websocket", "sse",
    "docker", "kubernetes", "k8s", "terraform",
    "openai", "anthropic", "gemini", "openrouter", "claude", "deepseek",
    "stripe", "razorpay", "paypal",
    "jwt", "oauth", "github oauth", "pat",
    "typescript", "python", "javascript",
    "pytest", "jest", "playwright",
    "vite", "webpack", "yarn", "npm",
)


def _extract_file_paths(text: str) -> list[str]:
    """Pull plausible file paths from prose. Dedupes, caps at 50."""
    if not text:
        return []
    found = _FILE_PATH_RX.findall(text)
    seen: set[str] = set()
    out: list[str] = []
    for p in found:
        if p in seen or len(p) > 200:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= 50:
            break
    return out


def _extract_stack_signals(text: str) -> list[str]:
    """Return de-duped lower-cased stack signals seen in `text`."""
    if not text:
        return []
    low = text.lower()
    return [s for s in _STACK_SIGNALS if s in low]


async def extract_session_patterns(
    *,
    db,
    user_id: str,
    project_id: Optional[str],
    session_id: str,
) -> Optional[dict]:
    """Fire-and-forget: mine the latest turns of `session_id` for hot
    files + stack signals and upsert into `ora_patterns`. Never raises.

    Returns the upsert payload on success (useful for tests), None on
    skip / failure.
    """
    try:
        if db is None or not user_id or not session_id:
            return None
        if os.environ.get("ORA_LEARNING_DISABLED") == "1":
            return None
        # Pull this session's recent turns. We only mine USER turns —
        # assistant replies parrot file names back and would skew counts.
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id},
            {"_id": 0, "turns": 1},
        )
        turns = (doc or {}).get("turns") or []
        if not turns:
            return None
        # Last 20 turns is plenty — older context is already baked into
        # the persisted pattern record.
        files_counter: Counter[str] = Counter()
        stack_counter: Counter[str] = Counter()
        for t in turns[-20:]:
            if (t.get("role") or "").lower() != "user":
                continue
            content = (t.get("content") or "")[:6000]
            for p in _extract_file_paths(content):
                files_counter[p] += 1
            for s in _extract_stack_signals(content):
                stack_counter[s] += 1

        if not files_counter and not stack_counter:
            return None

        hot_files = [p for p, _ in files_counter.most_common(10)]
        stack_signals = [s for s, _ in stack_counter.most_common(20)]

        payload = {
            "user_id":       user_id,
            "project_id":    project_id or "home",
            "hot_files":     hot_files,
            "stack_signals": stack_signals,
            "last_session":  session_id,
            "last_seen":     time.time(),
        }
        # Upsert: $inc session_count, $set the latest pattern snapshot.
        await db.ora_patterns.update_one(
            {"user_id": user_id, "project_id": project_id or "home"},
            {
                "$set": payload,
                "$inc": {"session_count": 1},
                "$setOnInsert": {"created_at": time.time()},
            },
            upsert=True,
        )
        return payload
    except Exception as e:                                   # noqa: BLE001
        logger.warning("extract_session_patterns failed: %r", e)
        return None


async def load_user_patterns(
    *,
    db,
    user_id: str,
    project_id: Optional[str],
) -> str:
    """Return a short system-prompt block describing what the user has
    been working on recently (hot files + stack). Empty string if no
    record exists. Never raises."""
    try:
        if db is None or not user_id:
            return ""
        rec = await db.ora_patterns.find_one(
            {"user_id": user_id, "project_id": project_id or "home"},
            {"_id": 0, "hot_files": 1, "stack_signals": 1, "session_count": 1},
        )
        if not rec:
            return ""
        hot_files = rec.get("hot_files") or []
        stack_signals = rec.get("stack_signals") or []
        if not hot_files and not stack_signals:
            return ""
        lines = ["[USER PATTERNS — learned across past sessions]"]
        if hot_files:
            lines.append("Hot files: " + ", ".join(hot_files[:10]))
        if stack_signals:
            lines.append("Stack signals: " + ", ".join(stack_signals[:12]))
        n = rec.get("session_count") or 0
        if n:
            lines.append(f"(across {n} past session{'s' if n != 1 else ''})")
        return "\n".join(lines)
    except Exception as e:                                   # noqa: BLE001
        logger.debug("load_user_patterns skipped: %r", e)
        return ""


__all__ = [
    "maybe_log_ora_escalation",
    "extract_session_patterns",
    "load_user_patterns",
]
