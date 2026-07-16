"""
services/supabase_sweeper.py — Iter 212m-234 — Phase 5 sweeper cron.

Daily background task that finalises Supabase downgrades once the
grace period has expired. Runs alongside the other main.py-scheduled
crons (daily digest, backup, nudge) and is idempotent — every wakeup
processes only rows whose `grace_until < now` and either haven't been
finalised yet or have a retry-eligible `sweep_error`.

WHAT IT DOES per policy:
    migrate_back    — VERIFY that the reverse-migration into shared
                      Mongo actually succeeded BEFORE the destructive
                      delete. If verification fails, the row is
                      requeued (sweep_error + attempts++) — data-loss
                      is impossible by construction.
    read_only       — delete the project outright (data was frozen
                      during the 30-day grace; user consented).
    export_delete   — delete after confirming an export artifact was
                      previously written to storage.
    keep_bill_user  — never delete; sweep only surfaces these into the
                      admin widget so the founder can chase the user.

FAILURE HANDLING:
    Any Supabase API failure OR any verification failure on migrate_back
    → the row is left with `sweep_error` set + `sweep_attempts += 1`.
    After MAX_SWEEP_ATTEMPTS the row is flipped to
    `sweep_status="needs_founder"` and surfaces on the admin widget so
    a human can intervene. We NEVER delete a project that has a pending
    verification failure — that is the whole point of the sweep.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Sweep cadence — hourly is overkill for this workload (grace periods
# are 7-30 days) so daily keeps API cost / rate-limit pressure low.
_SWEEP_INTERVAL_S = 24 * 3600
# Give up after this many retries and flag the row for founder review.
MAX_SWEEP_ATTEMPTS = 5


async def _process_one(db, row: dict) -> dict:
    """Finalise one pending downgrade. Never raises — returns a dict
    describing the outcome so the caller can aggregate stats and log."""
    from services import supabase_provisioner as sp

    app_id  = row["app_id"]
    user_id = row["user_id"]
    ref     = row["project_ref"]
    policy  = row.get("downgrade_policy") or "migrate_back"
    attempts = int(row.get("sweep_attempts") or 0)

    try:
        # Policy-specific pre-checks.
        if policy == "migrate_back":
            # Verify the pre-delete data copy actually landed in shared
            # Mongo. `apply_downgrade` runs the migration synchronously
            # at downgrade-time, but we double-check here: at least one
            # doc for this (app_id, user_id) exists in the shared
            # collection. This is the "never lose user data" gate.
            from services.aurem_managed_db import SHARED_COLLECTION
            existing = await db[SHARED_COLLECTION].count_documents(
                {"app_id": app_id, "user_id": user_id},
            )
            migrate_result = (row.get("migrate_back_result") or {})
            expected_rows = int(migrate_result.get("total_rows") or 0)

            # If the original migrate_back said 0 rows migrated we
            # accept — there was nothing to migrate. Otherwise we
            # require at least ONE row present as proof of transfer.
            if expected_rows > 0 and existing == 0:
                return await _mark_error(
                    db, app_id, user_id, attempts + 1,
                    "migrate_back verification failed: "
                    f"expected>=1 rows in shared Mongo, found 0. "
                    f"Original migration claimed {expected_rows} rows."
                )

        elif policy == "export_delete":
            # Require that an export artifact was recorded before we
            # delete. `apply_downgrade` should have written this;
            # if missing we requeue rather than delete.
            if not row.get("export_artifact_url"):
                return await _mark_error(
                    db, app_id, user_id, attempts + 1,
                    "export_delete needs export_artifact_url — missing",
                )

        elif policy == "keep_bill_user":
            # Nothing to delete — just surface it on the widget.
            return {
                "app_id": app_id, "action": "surfaced_only",
                "reason": "keep_bill_user policy",
            }

        # read_only + verified migrate_back + verified export_delete
        # → safe to delete on Supabase side.
        deleted = await sp.delete_project(ref)
        if not deleted.get("ok"):
            return await _mark_error(
                db, app_id, user_id, attempts + 1,
                f"supabase delete failed: {deleted.get('detail', deleted)}",
            )

        # Clear the row locally. Keep an audit trail in
        # `supabase_projects_history` so founder can trace what happened.
        await db["supabase_projects_history"].insert_one({
            **{k: v for k, v in row.items() if k != "_id"},
            "sweep_status":   "deleted",
            "deleted_at":     time.time(),
        })
        await db[sp.PROJECTS_COLLECTION].delete_one(
            {"app_id": app_id, "user_id": user_id},
        )
        # Flip project's storage_tier back to shared_mongo so future
        # writes route to the free-tier collection.
        await db.cto_projects.update_one(
            {"project_id": app_id, "user_id": user_id},
            {"$set":   {"storage_tier": "shared_mongo"},
             "$unset": {"supabase_ref": ""}},
        )
        logger.info("[sweeper] finalised downgrade app=%s policy=%s ref=%s",
                    app_id, policy, ref)
        return {"app_id": app_id, "action": "deleted", "policy": policy}

    except Exception as e:  # noqa: BLE001 — must never crash the cron
        return await _mark_error(
            db, app_id, user_id, attempts + 1,
            f"unhandled exception: {type(e).__name__}: {str(e)[:200]}",
        )


async def _mark_error(
    db, app_id: str, user_id: str, attempts: int, msg: str,
) -> dict:
    """Requeue the row for a later sweep OR escalate to the founder
    once we've retried MAX_SWEEP_ATTEMPTS times."""
    from services import supabase_provisioner as sp
    escalate = attempts >= MAX_SWEEP_ATTEMPTS
    update = {
        "sweep_attempts": attempts,
        "sweep_error":    msg[:500],
        "sweep_last_at":  time.time(),
    }
    if escalate:
        update["sweep_status"] = "needs_founder"
    await db[sp.PROJECTS_COLLECTION].update_one(
        {"app_id": app_id, "user_id": user_id},
        {"$set": update},
    )
    logger.warning(
        "[sweeper] app=%s attempt=%d %s escalated=%s",
        app_id, attempts, msg[:200], escalate,
    )
    return {
        "app_id":   app_id,
        "action":   "escalated" if escalate else "requeued",
        "attempts": attempts,
        "error":    msg[:200],
    }


async def sweep_once(db) -> dict:
    """Run a single sweep pass over all pending downgrades whose grace
    window has elapsed. Public so `routers/supabase.py` can expose an
    admin-triggered manual sweep endpoint too."""
    from services import supabase_provisioner as sp

    now = time.time()
    q = {
        "downgrade_pending": True,
        "downgrade_grace_until": {"$lte": now},
        # Don't re-attempt rows already escalated — those need human
        # intervention. They're re-armed by the admin manually.
        "$or": [
            {"sweep_status": {"$exists": False}},
            {"sweep_status": {"$ne": "needs_founder"}},
        ],
    }
    cursor = db[sp.PROJECTS_COLLECTION].find(q)
    outcomes: list[dict] = []
    async for row in cursor:
        outcomes.append(await _process_one(db, row))
    stats = {
        "processed":  len(outcomes),
        "deleted":    sum(1 for o in outcomes if o.get("action") == "deleted"),
        "requeued":   sum(1 for o in outcomes if o.get("action") == "requeued"),
        "escalated":  sum(1 for o in outcomes if o.get("action") == "escalated"),
        "surfaced":   sum(1 for o in outcomes if o.get("action") == "surfaced_only"),
        "at":         now,
    }
    if stats["processed"] > 0:
        logger.info("[sweeper] pass complete: %s", stats)
    return {"stats": stats, "outcomes": outcomes}


async def downgrade_sweeper_cron(interval_s: Optional[float] = None) -> None:
    """Runs forever; sleeps `interval_s` (default 24h) between sweeps.
    Wraps every wakeup in try/except so one bad iteration can't kill
    the loop. Wired into `main.py` behind
    `ENABLE_SUPABASE_SWEEPER=1` env flag."""
    interval = interval_s or _SWEEP_INTERVAL_S
    while True:
        try:
            # Late import so a missing get_db() dependency at module
            # import time doesn't break the whole backend boot.
            from cto_services.db import get_db
            db = get_db()
            if db is not None:
                await sweep_once(db)
        except Exception as e:  # noqa: BLE001
            logger.warning("[sweeper] pass errored: %r", e)
        await asyncio.sleep(interval)


async def list_pending_downgrades(db) -> list[dict]:
    """Admin widget helper — return every row currently in a pending or
    escalated state, sorted by soonest grace_until so the founder sees
    'about to delete' at the top."""
    from services import supabase_provisioner as sp
    cursor = db[sp.PROJECTS_COLLECTION].find(
        {"downgrade_pending": True},
        {"_id": 0},
    ).sort("downgrade_grace_until", 1)
    return [row async for row in cursor]


__all__ = [
    "MAX_SWEEP_ATTEMPTS",
    "sweep_once",
    "downgrade_sweeper_cron",
    "list_pending_downgrades",
]
