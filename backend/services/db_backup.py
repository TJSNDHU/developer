"""
services/db_backup.py — Iter 153.

Lightweight nightly backup of the operational MongoDB.

Strategy:
  • Spawn `mongodump` as a background subprocess from MONGO_URL
  • Output → /tmp/backups/aurem_YYYYMMDD_HHMMSS/
  • Keep the most recent 7 directories, prune the rest
  • Schedule once-per-day at 03:00 UTC via an asyncio task

This is not a substitute for managed snapshots in production — it is
an operator safety net so we never lose more than 24h of data even if
the upstream provider hiccups. Failure to back up never crashes the
app: we log and move on.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("db_backup")

BACKUP_ROOT = Path("/tmp/backups")
KEEP_DAYS = 7


def _prune_old(root: Path, keep: int) -> None:
    if not root.exists():
        return
    dirs = sorted(
        [p for p in root.iterdir() if p.is_dir() and p.name.startswith("aurem_")],
        reverse=True,
    )
    for old in dirs[keep:]:
        try:
            shutil.rmtree(old, ignore_errors=True)
            logger.info("pruned old backup %s", old.name)
        except Exception as e:
            logger.warning("prune failed for %s: %r", old, e)


def run_backup() -> Path | None:
    """Run a single mongodump now. Returns the dump dir on success."""
    mongo_url = os.environ.get("MONGO_URL")
    if not mongo_url:
        logger.warning("MONGO_URL missing — skipping backup")
        return None
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = BACKUP_ROOT / f"aurem_{stamp}"
    try:
        # mongodump streams a BSON dump tree. We swallow stderr into the
        # logger so a missing `mongodump` binary is obvious in `journalctl`.
        proc = subprocess.run(
            ["mongodump", f"--uri={mongo_url}", f"--out={out_dir}"],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            logger.error(
                "mongodump rc=%s stderr=%s", proc.returncode, proc.stderr[:400],
            )
            return None
        _prune_old(BACKUP_ROOT, KEEP_DAYS)
        logger.info("backup OK → %s", out_dir)
        return out_dir
    except FileNotFoundError:
        logger.warning("mongodump binary not installed — backup skipped")
    except subprocess.TimeoutExpired:
        logger.error("mongodump timed out after 600s")
    except Exception as e:
        logger.error("backup failed: %r", e)
    return None


async def backup_cron() -> None:
    """Awaitable that runs forever, kicking `run_backup` once per day
    at ~03:00 UTC. Designed to be launched from FastAPI lifespan with
    `asyncio.create_task(backup_cron())` so it dies with the process."""
    while True:
        try:
            now = dt.datetime.utcnow()
            # Next 03:00 UTC — tomorrow if we've already passed today.
            target = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if target <= now:
                target += dt.timedelta(days=1)
            sleep_s = max(60.0, (target - now).total_seconds())
            logger.info("next backup in %.0fs (target %s UTC)", sleep_s, target)
            await asyncio.sleep(sleep_s)
            await asyncio.to_thread(run_backup)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("backup_cron loop error: %r — sleeping 1h", e)
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise


__all__ = ["run_backup", "backup_cron"]
