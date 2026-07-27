"""services/integration_health_cron.py — Iter 328 · #5

Periodic probe of every configured integration (Stripe, Tavily,
Firecrawl, DeepSeek, ...).  Pre-Iter-328 the daily digest ran probes
once every 24h at 06:00 UTC + admin could manually refresh via the
POST /admin/integrations/refresh endpoint.  Breakages could go 24h
undetected.

This cron closes that gap:
  • Runs `run_all_probes()` every INTEGRATION_HEALTH_INTERVAL_SEC
    (default 600s = 10 min).
  • Persists each snapshot to `integration_health` (`_id: "latest"`)
    AND appends to `integration_health_history` so the founder can
    see how a status flipped over the day.
  • Env-gated: ENABLE_INTEGRATION_HEALTH_CRON default "1" (ON).
    Set to "0" to disable without a code change.
  • Fail-open: any probe or persist failure just logs at warning
    level — the loop keeps ticking.

Integrates with existing consumers:
  • daily_digest already writes `integration_health` + history using
    the same shape → this cron uses the same writer helper, so no
    schema divergence.
  • /admin/architecture reads `integration_health.latest` — the cron
    writes there → the tile stays fresh automatically.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)


def _interval_seconds() -> int:
    raw = (os.environ.get("INTEGRATION_HEALTH_INTERVAL_SEC") or "600").strip()
    try:
        v = int(raw)
        return max(60, v)  # never faster than every 60s — avoid runaway
    except ValueError:
        return 600


def _is_enabled() -> bool:
    v = (os.environ.get("ENABLE_INTEGRATION_HEALTH_CRON") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


async def _is_paused_by_flag() -> bool:
    """Iter 328 · #11 — feature-flag runtime kill-switch.

    Returns True ONLY when the flag doc exists AND explicitly
    `enabled=False`. Missing/absent flag → allow (default ON so
    behavior matches the env-only gate for anyone who hasn't seeded
    the flag yet). Fail-open on any error — never trips the cron off
    by accident.

    Contract: env `ENABLE_INTEGRATION_HEALTH_CRON` is the boot-time
    kill-switch. This flag is the runtime kill-switch. Both must
    allow for a probe to run.
    """
    try:
        from services.feature_flags import _load_flags
        flags = await _load_flags()
        doc = flags.get("integration_health_cron")
        if not doc:
            return False   # missing → don't pause
        return not doc.get("enabled", True)
    except Exception as e:                                  # noqa: BLE001
        logger.debug("feature_flag check failed, defaulting to allow: %r", e)
        return False


async def _probe_and_persist_once(db) -> Optional[dict]:
    """Single probe cycle. Returns the snapshot dict on success, None
    on failure. Never raises."""
    try:
        from services.integration_health import run_all_probes, summary_counts
        results = await run_all_probes()
        snap = {
            "results":      results,
            "summary":      summary_counts(results),
            "generated_at": time.time(),
            "trigger":      "periodic_cron",
        }
        await db.integration_health.update_one(
            {"_id": "latest"}, {"$set": snap}, upsert=True,
        )
        await db.integration_health_history.insert_one({
            **snap, "_id": f"snap_{int(snap['generated_at'])}",
        })
        counts = snap["summary"]
        logger.info(
            "🩺 integration_health cron · %s/%s ok · %s warn · %s broken · %s missing",
            counts["ok"], counts["total"],
            counts["warn"], counts["broken"], counts["missing"],
        )
        return snap
    except Exception as e:                                  # noqa: BLE001
        logger.warning("integration_health cron probe failed: %r", e)
        return None


async def schedule_integration_health_cron() -> None:
    """Background scheduler — sleeps INTERVAL_SEC between probes.
    Kicked off from main.py startup."""
    if not _is_enabled():
        logger.info("integration_health cron disabled via env")
        return

    interval = _interval_seconds()
    logger.info("🩺 integration_health cron ON · every %ds", interval)

    # First probe: wait a small stagger so the app can finish booting
    # before we hammer the LLMs. This also lets the daily_digest / cold-
    # start probe finish first if they were triggered on same boot.
    await asyncio.sleep(30)

    while True:
        try:
            # Iter 328 · #11 — runtime kill-switch via feature flag.
            # If founder toggles integration_health_cron OFF in
            # /admin/feature-flags, this branch skips the probe.
            # Admin toggle invalidates the flag cache so pause takes
            # effect within one cycle.
            if await _is_paused_by_flag():
                logger.debug("integration_health cron paused by feature flag")
            else:
                from cto_services.db import get_db
                db = get_db()
                if db is not None:
                    await _probe_and_persist_once(db)
        except Exception as e:                              # noqa: BLE001
            logger.warning("integration_health cron tick failed: %r", e)
        await asyncio.sleep(interval)
