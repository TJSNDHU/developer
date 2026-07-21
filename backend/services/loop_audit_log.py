"""
services/loop_audit_log.py — Iter 272 Feature 1.5

Central sink for every check-event during a loop run — pass, fail,
skip, retry, swallow. Anywhere the pipeline currently has an
`except: pass` or a "we caught it and moved on" branch, we route a
row through this module so it becomes visible in the audit trail.

Design goals:
  * Cheap — one Motor insert per event, no formatting drama.
  * Non-raising — a failure to log must never break the loop itself.
  * Structured — fields are enum-like strings so drift jobs can
    aggregate without free-text parsing.

Public surface:
    async log(db, *, loop_id, phase, kind, verdict, detail=None,
              retryable=False) → None
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_COLL = "loop_run_log"

# Documented event kinds. Not enforced at insert-time (Mongo is
# schemaless) but linted here for anyone using the module.
KIND_VANGUARD          = "vanguard"
KIND_INDEPENDENT       = "independent_verifier"
KIND_TEST_TOUCH        = "test_file_touch"
KIND_SILENT_CATCH      = "silent_exception_swallowed"
KIND_SHIP_GATE         = "ship_gate"
KIND_HUMAN_REVIEW_HOLD = "human_review_hold"
KIND_RETRY             = "retry"

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_SKIP = "skip"
VERDICT_WARN = "warn"


async def log(db, *,
              loop_id: str,
              phase: str,
              kind: str,
              verdict: str,
              detail: Optional[dict] = None,
              retryable: bool = False) -> None:
    """Write one audit row. Absolutely never raises."""
    try:
        row = {
            "loop_id":    loop_id,
            "phase":      phase,
            "kind":       kind,
            "verdict":    verdict,
            "retryable":  bool(retryable),
            "detail":     detail or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db[_COLL].insert_one(row)
    except Exception as e:                                    # noqa: BLE001
        # We cannot recursively call log() here — that would infinite-
        # loop on the same Mongo issue. Fall back to stderr via the
        # standard logger, which is where sysadmins look anyway.
        logger.warning(
            "loop_audit_log.log failed for %s (%s/%s): %r",
            loop_id, phase, kind, e,
        )


async def ensure_indexes(db) -> None:
    await db[_COLL].create_index("loop_id")
    await db[_COLL].create_index("created_at")
    await db[_COLL].create_index([("loop_id", 1), ("created_at", 1)])
    await db[_COLL].create_index("verdict")
