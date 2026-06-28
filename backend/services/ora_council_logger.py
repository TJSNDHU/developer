"""
services/ora_council_logger.py
Logs every chat, advice session, and code task to `ora_council_logs`
for future fine-tuning. Inserts are fire-and-forget — never block the
user-facing response.
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Literal
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Log builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_log(
    mode: str,
    user_message: str,
    final_output: str,
    agent_used: str,
    **kwargs,
) -> dict:
    return {
        "mode": mode,
        "user_message": user_message[:2000],    # cap to save storage
        "final_output": final_output[:4000],
        "agent_used": agent_used,
        "repo_context":         kwargs.get("repo_context"),
        "deepseek_draft":       (kwargs.get("deepseek_draft") or "")[:4000],
        "claude_correction":    (kwargs.get("claude_correction") or "")[:4000],
        "correction_applied":   kwargs.get("correction_applied", False),
        "pass_result":          kwargs.get("pass_result"),
        "lint_blocked":         kwargs.get("lint_blocked", False),
        "lint_issues":          kwargs.get("lint_issues", [])[:10],
        "parallelized":         kwargs.get("parallelized", False),
        "agents_used_count":    kwargs.get("agents_used_count", 1),
        "task_id":              kwargs.get("task_id"),
        "user_id":              kwargs.get("user_id"),
        "project_id":           kwargs.get("project_id"),
        "maxx_mode":            kwargs.get("maxx_mode", False),
        "ora_version":          "2.0",
        "timestamp":            datetime.now(timezone.utc),
        "exported_for_training": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Write helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _insert(db: AsyncIOMotorDatabase, doc: dict) -> None:
    """Fire-and-forget insert. Never raises."""
    try:
        await db["ora_council_logs"].insert_one(doc)
    except Exception as e:
        logger.warning("council_logger insert failed: %r", e)


async def log_conversational(
    db: AsyncIOMotorDatabase,
    mode: str,   # "A" | "B" | "D" | "E"
    user_message: str,
    ora_reply: str,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
):
    """Log Mode A (chat) / B (advice) / D (debug) / E (audit)."""
    doc = _build_log(
        mode=mode,
        user_message=user_message,
        final_output=ora_reply,
        agent_used="ora",
        user_id=user_id,
        project_id=project_id,
    )
    asyncio.create_task(_insert(db, doc))   # non-blocking


async def log_code_task(
    db: AsyncIOMotorDatabase,
    user_message: str,
    repo_context: str,
    deepseek_draft: str,
    final_output: str,
    correction_applied: bool,
    pass_result: bool,
    claude_correction: Optional[str] = None,
    lint_blocked: bool = False,
    lint_issues: Optional[list] = None,
    parallelized: bool = False,
    agents_used_count: int = 1,
    task_id: Optional[str] = None,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    maxx_mode: bool = False,
):
    """Log Mode C (code task). Call after gh_api_commit."""
    doc = _build_log(
        mode="C",
        user_message=user_message,
        final_output=final_output,
        agent_used="deepseek+claude" if maxx_mode else "deepseek",
        repo_context=repo_context,
        deepseek_draft=deepseek_draft,
        claude_correction=claude_correction,
        correction_applied=correction_applied,
        pass_result=pass_result,
        lint_blocked=lint_blocked,
        lint_issues=lint_issues or [],
        parallelized=parallelized,
        agents_used_count=agents_used_count,
        task_id=task_id,
        user_id=user_id,
        project_id=project_id,
        maxx_mode=maxx_mode,
    )
    asyncio.create_task(_insert(db, doc))


# ─────────────────────────────────────────────────────────────────────────────
# Stats — for admin panel
# ─────────────────────────────────────────────────────────────────────────────

async def get_council_stats(db: AsyncIOMotorDatabase) -> dict:
    """Returns ORA learning stats for admin dashboard.
    Called from admin router GET /admin/ora-stats"""
    total       = await db["ora_council_logs"].count_documents({})
    mode_a      = await db["ora_council_logs"].count_documents({"mode": "A"})
    mode_b      = await db["ora_council_logs"].count_documents({"mode": "B"})
    mode_c      = await db["ora_council_logs"].count_documents({"mode": "C"})
    mode_d      = await db["ora_council_logs"].count_documents({"mode": "D"})
    mode_e      = await db["ora_council_logs"].count_documents({"mode": "E"})
    corrections = await db["ora_council_logs"].count_documents({"correction_applied": True})
    lint_blocks = await db["ora_council_logs"].count_documents({"lint_blocked": True})
    parallel    = await db["ora_council_logs"].count_documents({"parallelized": True})
    exported    = await db["ora_council_logs"].count_documents({"exported_for_training": True})

    return {
        "total_interactions":    total,
        "by_mode": {
            "A_chat":   mode_a,
            "B_advice": mode_b,
            "C_code":   mode_c,
            "D_debug":  mode_d,
            "E_audit":  mode_e,
        },
        "corrections_applied":   corrections,
        "correction_rate_pct":   round(corrections / max(mode_c, 1) * 100, 1),
        "lint_blocks_caught":    lint_blocks,
        "parallel_tasks_run":    parallel,
        "exported_for_training": exported,
        "pending_export":        total - exported,
        # Iter 212m-77 — Self-learning is ACTIVE via RAG retrieval at
        # any N>=5. The 1,000-row threshold is now only for the
        # OPTIONAL fine-tune ship; RAG runs today regardless.
        "self_learning_active":  total >= 5,
        "self_learning_mode":    "rag_retrieval",
        "ready_for_finetune":    total >= 1000,
        "finetune_tip": (
            "Ready — export logs and submit to fine-tuning pipeline"
            if total >= 1000
            else f"Collect {1000 - total} more interactions for the "
                 f"OPTIONAL fine-tune ship. RAG self-learning is "
                 f"already live."
        ),
        "retriever":             _retriever_stats_safe(),
    }


def _retriever_stats_safe() -> dict:
    """Pulls the live RAG retriever state without raising on import-time
    failure (e.g. during unit tests that don't load the full app)."""
    try:
        from services.ora_council_retriever import get_retriever_stats
        return get_retriever_stats()
    except Exception:
        return {"active": False, "corpus_rows": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Daily export (JSONL for fine-tuning)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Index ensure — called on app startup (idempotent)
# ─────────────────────────────────────────────────────────────────────────────

async def ensure_indexes():
    """Idempotent. Called on app startup from main.py lifespan."""
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is None:
            return
        await db.ora_council_logs.create_index([("timestamp", -1)])
        await db.ora_council_logs.create_index("mode")
        await db.ora_council_logs.create_index("exported_for_training")
        await db.ora_council_logs.create_index("project_id")
        await db.ora_council_logs.create_index("user_id")
        await db.project_brains.create_index("project_id", unique=True)
        await db.issues_cache.create_index("repo", unique=True)
        # 1-hour TTL on issues cache
        try:
            await db.issues_cache.create_index(
                "fetched_at",
                expireAfterSeconds=3600,
                name="issues_cache_ttl",
            )
        except Exception:
            pass  # index might already exist with different options
        logger.info("ora_council indexes ensured")
    except Exception as e:
        logger.warning("ora_council ensure_indexes failed: %r", e)


ORA_SYSTEM_PROMPT = (
    "You are ORA, an autonomous engineering assistant. "
    "You help developers ship code directly to their GitHub repositories. "
    "You understand code, architecture, and give precise, actionable answers."
)

async def export_daily_jsonl(
    db: AsyncIOMotorDatabase,
    output_path: str = "./ora_training_data/latest.jsonl",
) -> dict:
    """
    Exports all un-exported logs as JSONL fine-tuning pairs.
    Schedule as daily cron: 0 0 * * *
    """
    import json, os
    from pathlib import Path

    os.makedirs(Path(output_path).parent, exist_ok=True)
    cursor = db["ora_council_logs"].find({"exported_for_training": False})
    logs   = await cursor.to_list(length=50_000)

    pairs = []
    ids   = []

    for log in logs:
        if not log.get("user_message") or not log.get("final_output"):
            continue
        pairs.append({
            "messages": [
                {"role": "system",    "content": ORA_SYSTEM_PROMPT},
                {"role": "user",      "content": log["user_message"]},
                {"role": "assistant", "content": log["final_output"]},
            ],
            "metadata": {
                "mode":             log.get("mode"),
                "correction":       log.get("correction_applied"),
                "parallelized":     log.get("parallelized"),
                "ora_version":      log.get("ora_version"),
            }
        })
        ids.append(log["_id"])

    if pairs:
        with open(output_path, "w") as f:
            for p in pairs:
                f.write(json.dumps(p, ensure_ascii=False, default=str) + "\n")
        await db["ora_council_logs"].update_many(
            {"_id": {"$in": ids}},
            {"$set": {"exported_for_training": True}},
        )

    return {"exported": len(pairs), "file": output_path}
