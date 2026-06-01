"""
services/ora_council_logger.py — Logs every ORA interaction for future fine-tune.

Modes:
  A = casual chat / greetings
  B = advice / suggestions / explanations
  C = real code task (Ship via CTO)

Collection: `ora_council_logs`
Never raises — logging failures must NEVER block the user-facing response.

Daily exporter reads from this collection to build JSONL training pairs
(see services/ora_learning_export.py).
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional, Literal, Any

logger = logging.getLogger(__name__)


def build_council_log(
    mode: Literal["A", "B", "C"],
    user_message: str,
    final_output: str,
    agent_used: str,
    repo_context: Optional[str] = None,
    deepseek_draft: Optional[Any] = None,
    claude_correction: Optional[Any] = None,
    correction_applied: bool = False,
    pass_result: Optional[bool] = None,
    issues_found: Optional[list] = None,
    task_id: Optional[str] = None,
    user_id: Optional[str] = None,
    maxx_mode: bool = False,
    session_id: Optional[str] = None,
) -> dict:
    return {
        "mode":               mode,
        "user_message":       (user_message or "")[:8000],
        "final_output":       (final_output or "")[:12000],
        "agent_used":         agent_used,
        "repo_context":       repo_context,
        "deepseek_draft":     deepseek_draft,
        "claude_correction":  claude_correction,
        "correction_applied": correction_applied,
        "pass_result":        pass_result,
        "issues_found":       (issues_found or [])[:20],
        "task_id":            task_id,
        "user_id":            user_id,
        "session_id":         session_id,
        "maxx_mode":          maxx_mode,
        "ora_version":        "1.0",
        "timestamp":          datetime.now(timezone.utc),
        "exported_for_training": False,
        "training_quality_score": None,
    }


def _get_db():
    """Lazy import so test environments without Mongo don't choke."""
    try:
        from cto_services.db import get_db
        return get_db()
    except Exception:
        return None


async def log_council_interaction(**kwargs) -> str:
    """Inserts one council log. Never raises."""
    db = _get_db()
    if db is None:
        return ""
    try:
        doc = build_council_log(**kwargs)
        result = await db.ora_council_logs.insert_one(doc)
        return str(result.inserted_id)
    except Exception as e:
        logger.warning("[ora_council] log failed: %r", e)
        return ""


async def log_conversational(
    mode: Literal["A", "B"],
    user_message: str,
    ora_reply: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    agent_used: str = "deepseek",
):
    """Mode A (chat) / Mode B (advice) — fire-and-forget logger."""
    await log_council_interaction(
        mode=mode,
        user_message=user_message,
        final_output=ora_reply,
        agent_used=agent_used,
        user_id=user_id,
        session_id=session_id,
    )


async def log_code_task(
    user_message: str,
    repo_context: str,
    deepseek_draft: Any,
    claude_correction: Optional[Any],
    final_output: Any,
    correction_applied: bool,
    pass_result: bool,
    issues_found: Optional[list] = None,
    task_id: Optional[str] = None,
    user_id: Optional[str] = None,
    maxx_mode: bool = False,
):
    """Mode C — full code-task log with both DeepSeek draft and Claude review."""
    # Stringify dicts so Mongo stores them cleanly even if they're nested
    def _stringify(v):
        if v is None or isinstance(v, str):
            return v
        try:
            import json
            return json.dumps(v, ensure_ascii=False, default=str)[:14000]
        except Exception:
            return str(v)[:14000]

    await log_council_interaction(
        mode="C",
        user_message=user_message,
        final_output=_stringify(final_output) or "",
        agent_used="deepseek+claude" if maxx_mode else "deepseek",
        repo_context=repo_context,
        deepseek_draft=_stringify(deepseek_draft),
        claude_correction=_stringify(claude_correction),
        correction_applied=correction_applied,
        pass_result=pass_result,
        issues_found=issues_found,
        task_id=task_id,
        user_id=user_id,
        maxx_mode=maxx_mode,
    )


async def ensure_indexes():
    """One-shot index creation. Safe to call repeatedly."""
    db = _get_db()
    if db is None:
        return
    try:
        await db.ora_council_logs.create_index([("timestamp", -1)])
        await db.ora_council_logs.create_index([("mode", 1)])
        await db.ora_council_logs.create_index([("exported_for_training", 1)])
        logger.info("ora_council_logs indexes ensured")
    except Exception as e:
        logger.warning("[ora_council] ensure_indexes failed: %r", e)
