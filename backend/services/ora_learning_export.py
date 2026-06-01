"""
services/ora_learning_export.py — Daily exporter for ORA training pairs.

Reads `ora_council_logs`, emits a JSONL fine-tune dataset per day, marks
exported rows so the next run is incremental.

Hook into daily_digest scheduler (already running at DIGEST_HOUR_UTC) or
call via /admin/ora/export manually.

Output schema (JSONL — one object per line):
{
  "messages": [
    {"role":"system",    "content":"<ORA system prompt>"},
    {"role":"user",      "content":"<user message>"},
    {"role":"assistant", "content":"<final correct output>"}
  ],
  "metadata": {...council log fields for debugging...}
}
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

EXPORT_DIR = Path(os.getenv("ORA_EXPORT_DIR", "/app/backend/ora_training_data"))
INCLUDE_MODE_A_B = True  # set False to train only on Mode C (code) pairs

ORA_SYSTEM_PROMPT = (
    "You are ORA, an autonomous engineering assistant.\n"
    "You help developers ship code directly to their GitHub repositories.\n"
    "You understand code, architecture, and give precise, actionable answers."
)


def _get_db():
    try:
        from cto_services.db import get_db
        return get_db()
    except Exception:
        return None


async def export_daily(date: Optional[datetime] = None) -> dict:
    """Exports the previous day's logs as a JSONL file.

    Returns {"exported": int, "file": str|None, "skipped": int}
    """
    db = _get_db()
    if db is None:
        return {"exported": 0, "file": None, "skipped": 0, "error": "no db"}

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    if not date:
        date = datetime.now(timezone.utc) - timedelta(days=1)
    day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end   = date.replace(hour=23, minute=59, second=59, microsecond=999999)

    query = {
        "timestamp": {"$gte": day_start, "$lte": day_end},
        "exported_for_training": False,
    }

    try:
        logs = await db.ora_council_logs.find(query).to_list(length=10_000)
    except Exception as e:
        logger.warning("[ora_export] query failed: %r", e)
        return {"exported": 0, "file": None, "skipped": 0, "error": str(e)}

    pairs = []
    skipped = 0
    for log in logs:
        pair = _build_training_pair(log)
        if pair is None:
            skipped += 1
            continue
        pairs.append(pair)

    if not pairs:
        return {"exported": 0, "file": None, "skipped": skipped}

    date_str  = date.strftime("%Y-%m-%d")
    file_path = EXPORT_DIR / f"ora_training_{date_str}.jsonl"
    with open(file_path, "w", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False, default=str) + "\n")

    log_ids = [log["_id"] for log in logs if "_id" in log]
    if log_ids:
        try:
            await db.ora_council_logs.update_many(
                {"_id": {"$in": log_ids}},
                {"$set": {"exported_for_training": True}},
            )
        except Exception as e:
            logger.warning("[ora_export] mark-exported failed: %r", e)

    logger.info("[ora_export] %s: %d pairs, %d skipped → %s",
                date_str, len(pairs), skipped, file_path)
    return {"exported": len(pairs), "file": str(file_path), "skipped": skipped}


def _build_training_pair(log: dict) -> Optional[dict]:
    mode         = log.get("mode")
    user_message = (log.get("user_message") or "").strip()
    final_output = (log.get("final_output") or "").strip()

    if not user_message or not final_output or len(final_output) < 20:
        return None

    if mode in ("A", "B") and not INCLUDE_MODE_A_B:
        return None
    if mode == "C":
        if log.get("pass_result") is None:
            return None  # incomplete log

    return {
        "messages": [
            {"role": "system",    "content": ORA_SYSTEM_PROMPT},
            {"role": "user",      "content": user_message},
            {"role": "assistant", "content": final_output},
        ],
        "metadata": {
            "mode":               mode,
            "agent_used":         log.get("agent_used"),
            "correction_applied": log.get("correction_applied"),
            "pass_result":        log.get("pass_result"),
            "repo_context":       log.get("repo_context"),
            "task_id":            str(log.get("task_id", "")),
            "timestamp":          str(log.get("timestamp", "")),
            "ora_version":        log.get("ora_version", "1.0"),
        },
    }


async def get_council_stats() -> dict:
    """Quick summary for /admin/ora/stats."""
    db = _get_db()
    if db is None:
        return {"error": "no db"}
    try:
        total = await db.ora_council_logs.count_documents({})
        mode_a = await db.ora_council_logs.count_documents({"mode": "A"})
        mode_b = await db.ora_council_logs.count_documents({"mode": "B"})
        mode_c = await db.ora_council_logs.count_documents({"mode": "C"})
        corr   = await db.ora_council_logs.count_documents({"correction_applied": True})
        exp    = await db.ora_council_logs.count_documents({"exported_for_training": True})
    except Exception as e:
        return {"error": str(e)}
    return {
        "total_interactions": total,
        "by_mode": {"A_chat": mode_a, "B_advice": mode_b, "C_code": mode_c},
        "corrections_applied": corr,
        "correction_rate_pct": round((corr / mode_c * 100) if mode_c else 0.0, 1),
        "exported_for_training": exp,
        "pending_export": total - exp,
        "ready_for_finetune": total >= 1000,
        "finetune_tip": (
            "Ready — export and submit to fine-tuning job"
            if total >= 1000
            else f"Need {1000 - total} more interactions before fine-tuning"
        ),
    }
