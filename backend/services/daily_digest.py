"""
services/daily_digest.py — Build & deliver the admin daily 1-pager.

`build_digest()` returns a dict ready for rendering / email body.
`schedule_daily_digest()` runs a background asyncio task that fires
once every 24h. The send hour is set by `DIGEST_HOUR_UTC` (default 6).

Email delivery: if `RESEND_API_KEY` is set, the digest is POSTed via
Resend's HTTP API. Otherwise we log it to stdout so admin can see it
in supervisor logs / Emergent's runtime logs.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from cto_services.db import get_db

logger = logging.getLogger(__name__)


async def build_digest() -> dict:
    """Pull last-24h numbers for the admin digest."""
    db = get_db()
    if db is None:
        return {"ok": False, "error": "db unavailable"}
    now = time.time()
    day_ago = now - 86400

    new_users = await db.dev_users.count_documents(
        {"created_at": {"$gte": day_ago}}
    )
    total_users = await db.dev_users.count_documents({})
    tasks_done = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": day_ago}, "status": "done"}
    )
    tasks_failed = await db.cto_tasks.count_documents(
        {"created_at": {"$gte": day_ago}, "status": "failed"}
    )
    chat_sessions = await db.chat_sessions.count_documents(
        {"updated_at": {"$gte": day_ago}}
    )
    open_tickets = await db.cto_support.count_documents(
        {"status": {"$in": ["open", "pending_user"]}}
    )

    # Token + cost (same proxy as Token P&L)
    pipe = [
        {"$match": {"created_at": {"$gte": day_ago}, "status": "done"}},
        {"$group": {"_id": None, "tokens": {"$sum": "$tokens_used"}}},
    ]
    tokens_used = 0
    async for d in db.cto_tasks.aggregate(pipe):
        tokens_used = d.get("tokens") or 0
    ai_cost = round((tokens_used / 1000) * 0.30, 2)

    # Top failed task (if any) — actionable signal for admin
    failed_sample = None
    if tasks_failed > 0:
        f = await db.cto_tasks.find_one(
            {"created_at": {"$gte": day_ago}, "status": "failed"},
            {"_id": 0, "task": 1, "error": 1, "task_id": 1},
        )
        if f:
            failed_sample = {
                "task_id": f.get("task_id"),
                "task": (f.get("task") or "")[:120],
                "error": (f.get("error") or "")[:200],
            }

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": 24,
        "users": {"new": new_users, "total": total_users},
        "tasks": {"done": tasks_done, "failed": tasks_failed},
        "chat_sessions": chat_sessions,
        "open_tickets": open_tickets,
        "ai_cost_usd": ai_cost,
        "tokens_used": tokens_used,
        "failed_sample": failed_sample,
    }


def _render_text(d: dict) -> str:
    """Plain-text version for log/email body."""
    if not d.get("ok"):
        return "(digest unavailable)"
    lines = [
        "AUREM CTO — Daily Digest",
        d["generated_at"],
        "─" * 40,
        f"New users (24h):  {d['users']['new']}   (total: {d['users']['total']})",
        f"Tasks done:       {d['tasks']['done']}",
        f"Tasks failed:     {d['tasks']['failed']}",
        f"Chat sessions:    {d['chat_sessions']}",
        f"Open tickets:     {d['open_tickets']}",
        f"AI cost (24h):    ${d['ai_cost_usd']}   ({d['tokens_used']} tokens)",
    ]
    if d.get("failed_sample"):
        f = d["failed_sample"]
        lines += [
            "",
            f"Sample failure: {f['task_id']}",
            f"  task : {f['task']}",
            f"  error: {f['error']}",
        ]
    return "\n".join(lines)


async def _send_via_resend(to_email: str, subject: str, body: str) -> bool:
    """POST the digest to Resend's send API. Returns True on success."""
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        return False
    sender = os.environ.get("DIGEST_FROM", "AUREM CTO <onboarding@resend.dev>")
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}"},
                json={"from": sender, "to": [to_email],
                       "subject": subject, "text": body},
            )
            r.raise_for_status()
            return True
    except Exception as e:
        logger.warning(f"resend send failed: {e!r}")
        return False


async def _run_once() -> None:
    digest = await build_digest()
    body = _render_text(digest)
    logger.info(f"📊 DAILY DIGEST\n{body}")
    admin_email = os.environ.get("ADMIN_EMAIL", "").strip()
    if admin_email:
        sent = await _send_via_resend(
            admin_email, "AUREM CTO — Daily Digest", body,
        )
        if not sent:
            logger.info(
                "RESEND_API_KEY not set — digest only logged. "
                "To enable email delivery, add RESEND_API_KEY to backend env."
            )

    # Iter 47 — daily ORA training JSONL export. Idempotent; safe to crash.
    try:
        from cto_services.db import get_db
        from services.ora_council_logger import export_daily_jsonl
        _db = get_db()
        if _db is not None:
            result = await export_daily_jsonl(_db)
            logger.info(
                f"📚 ORA daily export: {result.get('exported', 0)} pairs → "
                f"{result.get('file', '(none)')}"
            )
    except Exception as e:
        logger.warning(f"ORA daily export failed: {e!r}")

    # Iter 98 — auto-refresh the integration-health snapshot. The admin
    # panel reads this from `integration_health.latest`; if we don't
    # refresh, the founder would see stale data.
    try:
        from cto_services.db import get_db
        from services.integration_health import run_all_probes, summary_counts
        _db = get_db()
        if _db is not None:
            results = await run_all_probes()
            snap = {
                "results":      results,
                "summary":      summary_counts(results),
                "generated_at": time.time(),
                "trigger":      "daily_auto",
            }
            await _db.integration_health.update_one(
                {"_id": "latest"}, {"$set": snap}, upsert=True,
            )
            await _db.integration_health_history.insert_one({
                **snap, "_id": f"snap_{int(snap['generated_at'])}",
            })
            counts = snap["summary"]
            logger.info(
                f"🩺 Integration health daily refresh: "
                f"{counts['ok']}/{counts['total']} ok, "
                f"{counts['warn']} warn, {counts['broken']} broken, "
                f"{counts['missing']} missing"
            )
    except Exception as e:
        logger.warning(f"integration health daily refresh failed: {e!r}")

    # Iter 102 — Maxx overage billing. Run on the 1st of each month
    # (UTC) only. Idempotent within the day: once we bill a user, their
    # overage_count resets to 0 so the next pass is a no-op.
    try:
        from datetime import datetime as _dt, timezone as _tz
        from cto_services.db import get_db
        if _dt.now(_tz.utc).day == 1:
            from services.billing_cron import bill_maxx_overages
            _db = get_db()
            if _db is not None:
                result = await bill_maxx_overages(_db)
                logger.info(
                    f"💵 Maxx overage cron: billed {result['billed']}/"
                    f"{result['processed']} users for ${result['total_revenue_usd']} "
                    f"({result['failed']} failed)"
                )
                # Stash the result for the admin Financials page audit trail.
                if _db is not None:
                    await _db.billing_cron_runs.insert_one(result)
    except Exception as e:
        logger.warning(f"Maxx overage cron failed: {e!r}")


async def schedule_daily_digest() -> None:
    """Background loop — sleeps until target UTC hour, fires once, repeats."""
    target_hour = int(os.environ.get("DIGEST_HOUR_UTC", "6"))
    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run = next_run + timedelta(days=1)
        wait_s = (next_run - now).total_seconds()
        logger.info(f"daily digest sleeping {int(wait_s/3600)}h until {next_run.isoformat()}")
        try:
            await asyncio.sleep(wait_s)
            await _run_once()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.exception(f"digest scheduler crash: {e!r}")
            # Don't tight-loop on crash
            await asyncio.sleep(3600)
